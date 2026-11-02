"""Offline Big Fish prototype. No network clients or model inference."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

INITIAL = {'traits': ['清亮灵动', '好奇活泼', '温柔易亲近', '有主见', '愿意讨论和修正'], 'identity': '养成型虚拟角色；不声称拥有真实生命或意识', 'creative_interest': '探索中'}

def now():
    return datetime.now(timezone.utc).isoformat()

def parse_utc(value):
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError('时间必须含时区')
    return parsed.astimezone(timezone.utc)

def finite(value, low, high, name):
    import math
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(name)
    return value

class Core:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS personality(version INTEGER PRIMARY KEY, data TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, before_version INTEGER NOT NULL, after_version INTEGER NOT NULL, instruction TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, layer TEXT NOT NULL CHECK(layer IN ('short','long')), content TEXT NOT NULL, source TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1), created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS states(kind TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(kind,name));
        CREATE TABLE IF NOT EXISTS policy(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS whitelist(kind TEXT NOT NULL CHECK(kind IN ('friend','group')), id TEXT NOT NULL, PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, name TEXT NOT NULL, cost REAL NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, result TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, price_version TEXT NOT NULL, input_units INTEGER NOT NULL, output_units INTEGER NOT NULL, cost REAL NOT NULL, created TEXT NOT NULL);
        ''')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO personality VALUES (1,?,?,?)', (json.dumps(INITIAL, ensure_ascii=False), '初始人格', now()))
            for k,v in {'paid_budget':0,'external_send':False,'activity_authorized':False,'rest':False,'activity_limit':3}.items():
                self.db.execute('INSERT OR IGNORE INTO policy VALUES (?,?)', (k,json.dumps(v)))
    def close(self): self.db.close()
    def personality(self):
        r=self.db.execute('SELECT * FROM personality ORDER BY version DESC LIMIT 1').fetchone()
        return {'version':r['version'], 'data':json.loads(r['data'])}
    def correct(self, instruction, data):
        if not instruction.strip() or not isinstance(data, dict) or set(data) != set(INITIAL): raise ValueError('correction requires instruction and complete personality')
        if not isinstance(data['traits'], list) or not all(isinstance(x,str) and x.strip() for x in data['traits']) or not all(isinstance(data[k],str) and data[k].strip() for k in ('identity','creative_interest')): raise ValueError('invalid personality')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.personality()['version']; new=old+1
            self.db.execute('INSERT INTO personality VALUES (?,?,?,?)',(new,json.dumps(data,ensure_ascii=False),instruction,now()))
            self.db.execute('INSERT INTO corrections(before_version,after_version,instruction,created) VALUES (?,?,?,?)',(old,new,instruction,now()))
        return new
    def remember(self, *args, **kwargs):
        raise RuntimeError('无作用域记忆接口已停用；使用 boundary.Store')
    def memories(self, *args, **kwargs):
        raise RuntimeError('无作用域记忆接口已停用；使用 boundary.Store')
    def delete_memory(self, *args, **kwargs):
        raise RuntimeError('无作用域记忆接口已停用；使用 boundary.Store')
    def state(self, kind, name, value, at=None):
        if kind not in ('emotion','interest') or not name.strip(): raise ValueError('invalid state')
        value=finite(value,0,1,'value'); at=at or now()
        at=parse_utc(at).isoformat()
        with self.db: self.db.execute('INSERT OR REPLACE INTO states VALUES (?,?,?,?)',(kind,name,value,at))
    def read_state(self, kind, name, at=None):
        r=self.db.execute('SELECT * FROM states WHERE kind=? AND name=?',(kind,name)).fetchone()
        if not r: return 0.0
        try: updated=parse_utc(r['updated'])
        except (ValueError, TypeError) as exc: raise ValueError('旧状态时间无时区或无效，需要人工修复: '+kind+'/'+name) from exc
        elapsed=max(0,(parse_utc(at or now())-updated).total_seconds())
        half_life=3600 if kind=='emotion' else 30*86400
        return r['value'] * 0.5 ** (elapsed/half_life)
    def policy(self, key):
        row=self.db.execute('SELECT value FROM policy WHERE key=?',(key,)).fetchone()
        if row is None: raise KeyError(key)
        return json.loads(row[0])
    def set_policy(self, key, value):
        if key in ('external_send','activity_authorized','rest'):
            if type(value) is not bool: raise ValueError(key)
        elif key=='paid_budget': value=finite(value,0,1e9,key)
        elif key=='activity_limit':
            if type(value) is not int or not 0<=value<=1000: raise ValueError(key)
        else: raise KeyError(key)
        with self.db: self.db.execute('UPDATE policy SET value=? WHERE key=?',(json.dumps(value),key))
    def allow(self, kind, identifier):
        if kind not in ('friend','group') or not str(identifier).strip(): raise ValueError('invalid whitelist entry')
        with self.db: self.db.execute('INSERT OR IGNORE INTO whitelist VALUES (?,?)',(kind,str(identifier)))
    def receive_simulated(self, kind, identifier, text):
        allowed=self.db.execute('SELECT 1 FROM whitelist WHERE kind=? AND id=?',(kind,str(identifier))).fetchone() is not None
        return {'accepted':allowed,'text':text if allowed else None,'simulated':True}
    def send(self, *args):
        raise NotImplementedError('外发适配器未实现；即使开启开关也不会发送')
    def schedule(self, name, cost=0):
        cost=finite(cost,0,1e9,'cost')
        if not name.strip(): raise ValueError('activity')
        with self.db:
            # The write lock serializes authorization and daily-count checks across processes.
            self.db.execute('BEGIN IMMEDIATE')
            if not self.policy('activity_authorized'): return '未授权'
            if self.policy('rest'): return '休息中'
            today=now()[:10]
            count=self.db.execute('SELECT count(*) FROM activity WHERE substr(created,1,10)=?',(today,)).fetchone()[0]
            if count>=self.policy('activity_limit'): return '今日活动次数已达上限'
            # No paid provider exists; never spend or reserve hypothetical funds.
            if cost>0: return '付费活动尚未实现'
            self.db.execute('INSERT INTO activity(name,cost,created) VALUES (?,?,?)',(name,cost,now()))
        return '已安排离线模拟活动'
    def video_route(self, video_id, subtitles='', needs_visual=False, difficult=False):
        if not video_id.strip(): raise ValueError('video id')
        payload={'video_id':video_id,'subtitles':subtitles,'needs_visual':bool(needs_visual),'difficult':bool(difficult),'version':1}
        key=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cached=self.db.execute('SELECT result FROM cache WHERE key=?',(key,)).fetchone()
        if cached: return dict(json.loads(cached[0]),cached=True)
        if not subtitles.strip(): stage='需要字幕或人工提供材料'
        elif difficult: stage='难例需授权升级，尚未调用模型'
        elif needs_visual: stage='需要关键帧，尚未抽帧或理解画面'
        else: stage='仅字幕材料，尚未调用模型'
        result={'stage':stage,'coverage':'仅字幕' if subtitles.strip() else '无','watched_video':False,'model_called':False,'cached':False}
        with self.db: self.db.execute('INSERT INTO cache VALUES (?,?,?)',(key,json.dumps(result,ensure_ascii=False),now()))
        return result
    def record_usage(self, provider, model, price_version, input_units, output_units, cost):
        # Only call after a real provider supplies usage; this is an accounting interface, not an API integration.
        if not all(isinstance(x,str) and x.strip() for x in (provider,model,price_version)): raise ValueError('provenance')
        if any(type(x) is not int or x<0 for x in (input_units,output_units)): raise ValueError('usage')
        cost=finite(cost,0,1e9,'cost')
        with self.db: self.db.execute('INSERT INTO ledger(provider,model,price_version,input_units,output_units,cost,created) VALUES (?,?,?,?,?,?,?)',(provider,model,price_version,input_units,output_units,cost,now()))
    def voice_parameters(self, content_mode='neutral'):
        # Output is a request to a future backend, not synthesized audio or an emotion classifier.
        modes={'neutral':(0.9,'平稳'),'gentle':(0.85,'轻柔'),'excited':(0.95,'活泼')}
        if content_mode not in modes: raise ValueError('content mode')
        rate,delivery=modes[content_mode]
        return {'voice_id':'A（待接入）','rate':rate,'delivery':delivery,'backend':None}
