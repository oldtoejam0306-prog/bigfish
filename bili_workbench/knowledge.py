"""Owner-entered local references; never retrieved or sent without opt-in."""
import re
import secrets
import time
import unicodedata
from .runtime import Fault


class KnowledgeBase:
    def __init__(self, state):
        self.s = state
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_knowledge (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL)')

    def add(self, title, body, source=''):
        for value, limit, label in ((title, 100, '标题'), (body, 6000, '内容'), (source, 200, '来源')):
            if not isinstance(value, str) or len(value) > limit or any(ord(ch) < 32 and ch not in '\n\t\r' for ch in value):
                raise Fault(label + '格式无效或过长')
        title, body, source = title.strip(), body.strip(), source.strip()
        if not title or not body:
            raise Fault('请填写标题和内容')
        if re.search(r'(?i)(?:\bsk-[a-z0-9_-]{12,}|\bSESSDATA\s*=|\bBearer\s+[a-z0-9._-]{12,})', '\n'.join((title, body, source))):
            raise Fault('内容可能包含凭据，请移除后再保存')
        with self.s.lock:
            if self.s.db.execute('SELECT COUNT(*) FROM local_knowledge').fetchone()[0] >= 200:
                raise Fault('本机知识库已满，请先删除不需要的资料')
            item_id = secrets.token_hex(12)
            self.s.db.execute('INSERT INTO local_knowledge VALUES (?,?,?,?,?)', (item_id, title, body, source, time.time()))
        return {'id': item_id, 'title': title}

    def list(self):
        with self.s.lock:
            rows = self.s.db.execute('SELECT id,title,source,created FROM local_knowledge ORDER BY created DESC,id LIMIT 200').fetchall()
        return {'items': [dict(row) for row in rows]}

    def get(self, item_id):
        self._validate_id(item_id)
        with self.s.lock:
            row = self.s.db.execute('SELECT id,title,body,source FROM local_knowledge WHERE id=?', (item_id,)).fetchone()
        if row is None: raise Fault('资料不存在或已删除')
        return dict(row)

    def delete(self, item_id):
        self._validate_id(item_id)
        with self.s.lock:
            changed = self.s.db.execute('DELETE FROM local_knowledge WHERE id=?', (item_id,)).rowcount
        if not changed: raise Fault('资料不存在或已删除')
        return {'deleted': True, 'note': '已从当前数据库移除；磁盘和备份中的残留无法保证立即清除。'}

    @staticmethod
    def _validate_id(item_id):
        if not isinstance(item_id, str) or not re.fullmatch(r'[0-9a-f]{24}', item_id): raise Fault('资料编号无效')

    def retrieve(self, query):
        text = unicodedata.normalize('NFKC', query).lower()
        terms = set(re.findall(r'[a-z0-9]{2,}', text))
        for sequence in re.findall(r'[\u4e00-\u9fff]+', text):
            terms.update(sequence[i:i+2] for i in range(len(sequence)-1))
        terms = sorted(terms, key=lambda x: (-len(x), x))[:50]
        if not terms: return []
        with self.s.lock:
            rows = self.s.db.execute('SELECT title,body,source FROM local_knowledge ORDER BY created DESC LIMIT 200').fetchall()
        matches = []
        for row in rows:
            haystack = unicodedata.normalize('NFKC', row['title'] + '\n' + row['body']).lower()
            found = [term for term in terms if term in haystack]
            if not found: continue
            original = row['body']
            at = next((original.lower().find(t) for t in found if t in original.lower()), 0)
            excerpt = original[max(0, at-80):max(0, at-80)+250]
            matches.append((len(found), row['title'], {'title': row['title'], 'source': row['source'], 'excerpt': excerpt}))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in matches[:2]]
