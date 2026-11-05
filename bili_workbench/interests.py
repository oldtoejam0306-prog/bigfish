"""Public character interests and metadata-only candidate selection.

No model, platform, Vault or owner-memory access. Trusted local callers must
explicitly publish feedback; untrusted video metadata never changes interests.
"""
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from boundary import decay
from .bili import BV
from .runtime import Fault, canonical, digest

SOURCES = ('owner_explicit_public', 'character_public_feedback')
COVERAGE = ('metadata', 'subtitle', 'sparse_frames', 'subtitle_and_frames', 'unavailable')
HALF_LIFE_SECONDS = 30 * 86400


def _at(value=None):
    value = datetime.now(timezone.utc).isoformat() if value is None else value
    if not isinstance(value, str):
        raise Fault('兴趣时间须为带时区的ISO文本')
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise Fault('兴趣时间无效') from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise Fault('兴趣时间须含时区')
    return dt.astimezone(timezone.utc).isoformat()


def _text(value, limit, label, nonempty=False):
    if not isinstance(value, str) or len(value) > limit or (nonempty and not value.strip()):
        raise Fault(label + '格式无效')
    return value.strip()


def _weight(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
        raise Fault('兴趣权重须为-1至1的有限数值')
    return float(value)


def _term(value):
    return unicodedata.normalize('NFKC', _text(value, 40, '公开兴趣词', True)).casefold()


def _matches(term, text):
    text = unicodedata.normalize('NFKC', text).casefold()
    if term.isascii() and any(c.isalnum() for c in term):
        return re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text) is not None
    return term in text


class Interests:
    """Uses State.db/lock; no second character/personality store is created."""
    def __init__(self, state):
        self.s = state
        with state.lock:
            state.db.executescript('''
            CREATE TABLE IF NOT EXISTS public_interest_topics(
                topic TEXT PRIMARY KEY,weight REAL NOT NULL,updated TEXT NOT NULL,
                source TEXT NOT NULL,evidence TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS public_interest_events(
                id INTEGER PRIMARY KEY,topic_hash TEXT NOT NULL,action TEXT NOT NULL,
                before_value REAL,after_value REAL,source TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS public_video_observations(
                account TEXT NOT NULL,bvid TEXT NOT NULL,coverage TEXT NOT NULL,
                source TEXT NOT NULL,material_id TEXT NOT NULL,created TEXT NOT NULL,
                PRIMARY KEY(account,bvid));
            CREATE TABLE IF NOT EXISTS public_interest_selections(
                id TEXT PRIMARY KEY,created TEXT NOT NULL,decisions TEXT NOT NULL);
            ''')

    def _account(self):
        return self.s.config()['account_mid'] or 'anonymous'

    def snapshot(self, now=None):
        at = _at(now)
        with self.s.lock:
            result = []
            for row in self.s.db.execute('SELECT * FROM public_interest_topics ORDER BY topic'):
                weight = row['weight']
                value = math.copysign(decay(abs(weight), row['updated'], at, HALF_LIFE_SECONDS), weight)
                result.append(dict(row, decayed_weight=value))
            return {'interests': result, 'as_of': at, 'scope': 'public_character_only',
                    'half_life_seconds': HALF_LIFE_SECONDS, 'owner_memory_read': False,
                    'model_called': False}

    def _change(self, topic, weight, source, evidence, at, action):
        if source not in SOURCES:
            raise Fault('兴趣来源必须为显式公开配置或角色公开反馈')
        topic = _term(topic); weight = _weight(weight)
        evidence = _text(evidence, 500, '公开来源说明', True); at = _at(at)
        with self.s.lock:
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                old = self.s.db.execute('SELECT weight,updated FROM public_interest_topics WHERE topic=?', (topic,)).fetchone()
                before = None if old is None else math.copysign(decay(abs(old['weight']), old['updated'], at, HALF_LIFE_SECONDS), old['weight'])
                if action == 'feedback':
                    weight = max(-1., min(1., (before or 0.) + weight))
                if old is None and self.s.db.execute('SELECT count(*) FROM public_interest_topics').fetchone()[0] >= 100:
                    raise Fault('公开兴趣最多100项')
                self.s.db.execute('INSERT OR REPLACE INTO public_interest_topics VALUES (?,?,?,?,?)', (topic, weight, at, source, evidence))
                self.s.db.execute('INSERT INTO public_interest_events(topic_hash,action,before_value,after_value,source,evidence_sha256,created) VALUES (?,?,?,?,?,?,?)',
                                  (digest(topic), action, before, weight, source, hashlib.sha256(evidence.encode()).hexdigest(), at))
                self.s.db.commit()
            except Exception:
                self.s.db.rollback(); raise
        return {'topic': topic, 'weight': weight, 'source': source, 'updated': at,
                'scope': 'public_character_only', 'not_owner_preference': True}

    def correct(self, topic, weight, reason, now=None):
        """Trusted local owner explicitly publishes/corrects a character interest."""
        return self._change(topic, weight, 'owner_explicit_public', reason, now, 'corrected')

    def feedback(self, topic, delta, *, source, evidence, now=None):
        """Not called by select(); callers must deliberately publish feedback."""
        return self._change(topic, delta, source, evidence, now, 'feedback')

    def delete(self, topic, reason, now=None):
        topic = _term(topic); reason = _text(reason, 500, '删除说明', True); at = _at(now)
        with self.s.lock:
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                row = self.s.db.execute('SELECT weight FROM public_interest_topics WHERE topic=?', (topic,)).fetchone()
                if row is None:
                    raise Fault('公开兴趣不存在')
                self.s.db.execute('DELETE FROM public_interest_topics WHERE topic=?', (topic,))
                for selection in self.s.db.execute('SELECT id,decisions FROM public_interest_selections').fetchall():
                    decisions = json.loads(selection['decisions'])
                    for decision in decisions:
                        decision['matches'] = [match for match in decision.get('matches', []) if match.get('topic') != topic]
                    self.s.db.execute('UPDATE public_interest_selections SET decisions=? WHERE id=?', (canonical(decisions), selection['id']))
                self.s.db.execute('INSERT INTO public_interest_events(topic_hash,action,before_value,after_value,source,evidence_sha256,created) VALUES (?,?,?,?,?,?,?)',
                                  (digest(topic), 'deleted', row['weight'], None, 'owner_explicit_public', hashlib.sha256(reason.encode()).hexdigest(), at))
                self.s.db.commit()
            except Exception:
                self.s.db.rollback(); raise
        return {'deleted': True, 'audit_contains_deleted_topic': False,
                'note': '逻辑数据删除与哈希审计；SQLite空闲页/备份不等于安全擦除'}

    def record_observation(self, bvid, coverage, source, material_id='', now=None):
        if not isinstance(bvid, str) or not BV.fullmatch(bvid) or coverage not in COVERAGE:
            raise Fault('公开视频来源或覆盖范围无效')
        source = _text(source, 200, '观察来源', True)
        material_id = _text(material_id, 200, '材料ID'); at = _at(now)
        with self.s.lock:
            self.s.db.execute('INSERT OR REPLACE INTO public_video_observations VALUES (?,?,?,?,?,?)',
                              (self._account(), bvid, coverage, source, material_id, at))
        return {'bvid': bvid, 'coverage': coverage, 'full_video_watched': False,
                'interest_changed': False, 'model_called': False}

    @staticmethod
    def _candidate(item):
        if not isinstance(item, dict) or not isinstance(item.get('bvid'), str) or not BV.fullmatch(item['bvid']):
            raise Fault('候选BV无效')
        title = _text(item.get('title', ''), 500, '候选标题', True)
        desc = _text(item.get('desc', ''), 10000, '候选简介')
        category = _text(item.get('tname', ''), 100, '候选分区')
        tags = item.get('tags', item.get('tag', []))
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.split(',') if part.strip()]
        if not isinstance(tags, list) or len(tags) > 30:
            raise Fault('候选标签无效')
        tags = [_text(tag, 40, '候选标签', True) for tag in tags]
        return {'bvid': item['bvid'], 'title': title, 'desc': desc, 'tname': category, 'tags': tags}

    def select(self, items, max_items=3, seen=(), explore_ratio=1/3, now=None, seed=None):
        """Return sanitized metadata and reasons. It never promotes metadata to memory.
        Seen observations mean attempted/observed, never "fully watched".
        """
        if not isinstance(items, list) or len(items) > 100:
            raise Fault('候选列表最多100条')
        if type(max_items) is not int or not 0 <= max_items <= 3:
            raise Fault('每轮最多选择3条')
        if type(explore_ratio) not in (int, float) or not math.isfinite(explore_ratio) or not 0 <= explore_ratio <= 1:
            raise Fault('探索比例须为0至1')
        if not isinstance(seen, (list, tuple, set, frozenset)) or any(not isinstance(x, str) or not BV.fullmatch(x) for x in seen):
            raise Fault('已观察列表无效')
        at = _at(now); seed = at[:10] if seed is None else _text(seed, 200, '选择种子')
        with self.s.lock:
            excluded = set(seen)
            excluded.update(row[0] for row in self.s.db.execute('SELECT bvid FROM public_video_observations WHERE account=?', (self._account(),)))
            interests = self.snapshot(at)['interests']; candidates = {}; rejected = 0
            for raw in items:
                candidate = self._candidate(raw); bvid = candidate['bvid']
                if bvid in excluded or bvid in candidates:
                    continue
                matches = []; score = 0.
                fields = (('title', 3.), ('tags', 4.), ('tname', 2.), ('desc', 1.))
                for interest in interests:
                    found = [field for field, _ in fields if _matches(interest['topic'], ' '.join(candidate[field]) if field == 'tags' else candidate[field])]
                    if found:
                        contribution = interest['decayed_weight'] * sum(multiplier for field, multiplier in fields if field in found)
                        score += contribution
                        matches.append({'topic': interest['topic'], 'fields': found, 'weight': interest['decayed_weight'], 'source': interest['source']})
                if score < 0:
                    rejected += 1; continue
                candidates[bvid] = {'item': candidate, 'score': score, 'matches': matches,
                                    'tie': digest([seed, bvid])}
            ranked = sorted(candidates.values(), key=lambda row: (-row['score'], row['tie']))
            n = min(max_items, len(ranked)); explore_n = min(n, math.ceil(n * explore_ratio)) if n > 1 or explore_ratio == 1 else 0
            chosen = [(row, 'interest' if row['matches'] else 'cold_start') for row in ranked[:n-explore_n]]
            chosen_ids = {row['item']['bvid'] for row, _ in chosen}
            pool = [row for row in ranked if row['item']['bvid'] not in chosen_ids]
            # Prefer genuinely unmatched topics for exploration; never override dislikes.
            pool.sort(key=lambda row: (bool(row['matches']), row['tie']))
            chosen += [(row, 'exploration') for row in pool[:explore_n]]
            decisions = [{'bvid': row['item']['bvid'], 'score': row['score'], 'strategy': strategy,
                          'matches': row['matches'], 'source': 'B站候选元数据',
                          'source_url': 'https://www.bilibili.com/video/' + row['item']['bvid'],
                          'coverage': '仅元数据筛选', 'full_video_watched': False} for row, strategy in chosen]
            selection_id = digest([self._account(), at, seed, decisions])
            self.s.db.execute('INSERT OR REPLACE INTO public_interest_selections VALUES (?,?,?)', (selection_id, at, canonical(decisions)))
            return {'items': [row['item'] for row, _ in chosen], 'decisions': decisions,
                    'selection_id': selection_id, 'coverage': '仅元数据筛选',
                    'model_called': False, 'owner_memory_read': False,
                    'excluded_disliked_count': rejected, 'automatic_outbound': False}
