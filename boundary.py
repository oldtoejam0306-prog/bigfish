"""Capability-limited offline boundary, no network clients or platform SDKs."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


def _scope(kind, identifier):
    if kind not in ('friend','group') or not isinstance(identifier,str) or not identifier.strip(): raise ValueError('invalid scope')
    return kind, identifier

def _time(value):
    value=datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None: raise ValueError('timezone required')
    return value.astimezone(timezone.utc)

def decay(value, updated, current, half_life_seconds):
    import math
    if not math.isfinite(value) or not 0 <= value <= 1 or not math.isfinite(half_life_seconds) or half_life_seconds <= 0: raise ValueError('invalid decay parameters')
    elapsed=max(0,(_time(current)-_time(updated)).total_seconds())
    return value*0.5**(elapsed/half_life_seconds)

class Store:
    def __init__(self, path):
        self.db=sqlite3.connect(str(path),timeout=30,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS scoped_memory(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, peer TEXT NOT NULL, layer TEXT NOT NULL, content TEXT, source TEXT NOT NULL, confidence REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS memory_events(id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL, action TEXT NOT NULL, source TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS budget_config(id INTEGER PRIMARY KEY CHECK(id=1), limit_micro INTEGER NOT NULL CHECK(limit_micro>=0));
        INSERT OR IGNORE INTO budget_config VALUES (1,0);
        CREATE TABLE IF NOT EXISTS reservations(request_id TEXT PRIMARY KEY, amount INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','settled','released')), actual INTEGER);
        CREATE TABLE IF NOT EXISTS analysis_cache(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_log(legacy_id INTEGER PRIMARY KEY, scope_kind TEXT NOT NULL, scope_peer TEXT NOT NULL, new_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS request_binding(request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS executions(request_id TEXT PRIMARY KEY, state TEXT NOT NULL);
        ''')
    def close(self): self.db.close()
    def add_memory(self,kind,peer,layer,content,source,confidence):
        _scope(kind,peer)
        import math
        if layer not in ('short','long') or not content.strip() or not source.strip() or not math.isfinite(confidence) or not 0<=confidence<=1: raise ValueError('memory')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            ident=self.db.execute('INSERT INTO scoped_memory(kind,peer,layer,content,source,confidence) VALUES (?,?,?,?,?,?)',(kind,peer,layer,content,source,confidence)).lastrowid
            self._event(ident,'created',source,'created')
            self.db.commit(); return ident
        except Exception: self.db.rollback(); raise
    def _event(self,ident,action,source,note):
        self.db.execute('INSERT INTO memory_events(memory_id,action,source,note,created) VALUES (?,?,?,?,?)',(ident,action,source,note,datetime.now(timezone.utc).isoformat()))
    def memories(self,kind,peer):
        _scope(kind,peer)
        return [dict(r) for r in self.db.execute('SELECT * FROM scoped_memory WHERE kind=? AND peer=? AND deleted=0 ORDER BY id',(kind,peer))]
    def change_memory(self,kind,peer,ident,source,new_content=None):
        _scope(kind,peer)
        if not source.strip() or (new_content is not None and not new_content.strip()): raise ValueError('source/content')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            r=self.db.execute('SELECT content FROM scoped_memory WHERE id=? AND kind=? AND peer=? AND deleted=0',(ident,kind,peer)).fetchone()
            if r is None: raise KeyError(ident)
            # Audit stores hashes, not the deleted or superseded plaintext.
            digest=hashlib.sha256(r['content'].encode()).hexdigest()
            if new_content is None:
                self.db.execute('UPDATE scoped_memory SET content=NULL,deleted=1 WHERE id=?',(ident,)); action='deleted'
            else:
                self.db.execute('UPDATE scoped_memory SET content=?,source=? WHERE id=?',(new_content,source,ident)); action='corrected'
            self._event(ident,action,source,'previous_sha256:'+digest)
            self.db.commit()
        except Exception: self.db.rollback(); raise
    def set_budget(self, micro):
        if type(micro) is not int or micro<0: raise ValueError('integer microcurrency required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            used=self._used()
            if micro<used: raise ValueError('below committed or reserved amount')
            self.db.execute('UPDATE budget_config SET limit_micro=? WHERE id=1',(micro,)); self.db.commit()
        except Exception: self.db.rollback(); raise
    def _used(self):
        return self.db.execute("SELECT COALESCE(SUM(CASE state WHEN 'reserved' THEN amount WHEN 'settled' THEN actual ELSE 0 END),0) FROM reservations").fetchone()[0]
    def reserve(self, request_id, amount):
        if not isinstance(request_id,str) or not request_id.strip() or type(amount) is not int or amount<0: raise ValueError('request')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT * FROM reservations WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['amount']!=amount: raise ValueError('idempotency key conflict')
                result=old['state']
            else:
                limit=self.db.execute('SELECT limit_micro FROM budget_config').fetchone()[0]
                if self._used()+amount>limit: raise ValueError('budget exceeded')
                self.db.execute("INSERT INTO reservations VALUES (?,?,'reserved',NULL)",(request_id,amount)); result='reserved'
            self.db.commit(); return result
        except Exception: self.db.rollback(); raise
    def settle(self, request_id, actual):
        if type(actual) is not int or actual<0: raise ValueError('actual')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            r=self.db.execute('SELECT * FROM reservations WHERE request_id=?',(request_id,)).fetchone()
            if r is None: raise KeyError(request_id)
            if r['state']=='settled':
                if r['actual']!=actual: raise ValueError('conflicting settlement')
            elif r['state']!='reserved' or actual>r['amount']: raise ValueError('invalid settlement')
            else: self.db.execute("UPDATE reservations SET state='settled',actual=? WHERE request_id=?",(actual,request_id))
            self.db.commit()
        except Exception: self.db.rollback(); raise
    def release(self,request_id):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            r=self.db.execute('SELECT state FROM reservations WHERE request_id=?',(request_id,)).fetchone()
            if not r: raise KeyError(request_id)
            if r['state']=='settled': raise ValueError('already settled')
            self.db.execute("UPDATE reservations SET state='released' WHERE request_id=?",(request_id,)); self.db.commit()
        except Exception: self.db.rollback(); raise
    def migrate_legacy(self, assignments):
        """Explicit mapping legacy integer ID -> (kind, peer); all others stay inaccessible."""
        if not isinstance(assignments,dict): raise ValueError('assignments')
        for ident,scope in assignments.items():
            if type(ident) is not int or not isinstance(scope,(tuple,list)) or len(scope)!=2: raise ValueError('assignment')
            _scope(*scope)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            table=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone()
            if not table and assignments: raise ValueError('no legacy table')
            for ident,(kind,peer) in assignments.items():
                old=self.db.execute('SELECT * FROM memories WHERE id=?',(ident,)).fetchone()
                if old is None: raise KeyError(ident)
                migrated=self.db.execute('SELECT * FROM migration_log WHERE legacy_id=?',(ident,)).fetchone()
                if migrated:
                    if (migrated['scope_kind'],migrated['scope_peer'])!=(kind,peer): raise ValueError('legacy scope conflict')
                    continue
                new_id=self.db.execute('INSERT INTO scoped_memory(kind,peer,layer,content,source,confidence) VALUES (?,?,?,?,?,?)',(kind,peer,old['layer'],old['content'],old['source'],old['confidence'])).lastrowid
                self._event(new_id,'migrated',old['source'],'explicit legacy id:'+str(ident))
                self.db.execute('INSERT INTO migration_log VALUES (?,?,?,?)',(ident,kind,peer,new_id))
            self.db.commit()
        except Exception: self.db.rollback(); raise
    def bind_request(self,request_id,payload_hash):
        if not request_id or not payload_hash: raise ValueError('request binding')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT payload_hash FROM request_binding WHERE request_id=?',(request_id,)).fetchone()
            if row and row[0]!=payload_hash: raise ValueError('request payload conflict')
            if not row: self.db.execute('INSERT INTO request_binding VALUES (?,?)',(request_id,payload_hash))
            self.db.commit()
        except Exception: self.db.rollback(); raise
    def cache_key(self,content,model_version,prompt_version):
        if not all(isinstance(v,str) and v for v in (content,model_version,prompt_version)): raise ValueError('cache inputs')
        data=json.dumps([content,model_version,prompt_version],ensure_ascii=False,separators=(',',':'))
        return hashlib.sha256(data.encode()).hexdigest()
    def cache_put(self,content,model_version,prompt_version,result):
        self.db.execute('INSERT OR REPLACE INTO analysis_cache VALUES (?,?)',(self.cache_key(content,model_version,prompt_version),json.dumps(result,ensure_ascii=False)))
    def cache_get(self,content,model_version,prompt_version):
        row=self.db.execute('SELECT value FROM analysis_cache WHERE key=?',(self.cache_key(content,model_version,prompt_version),)).fetchone()
        return json.loads(row[0]) if row else None

@dataclass(frozen=True)
class Subtitle:
    video_id: str
    text: str
    source: str
    coverage: str = 'subtitle_only'
    metadata: dict | None = None

class ReadOnlySubtitleProvider(Protocol):
    def get_subtitle(self, video_id: str) -> Subtitle: ...

class FixtureSubtitleProvider:
    """Fixture, not an upstream implementation; the object has no send or mutation methods."""
    def __init__(self, fixtures): self._fixtures=dict(fixtures)
    @property
    def revision(self):
        return hashlib.sha256(json.dumps(self._fixtures,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def get_subtitle(self,video_id):
        if video_id not in self._fixtures: raise KeyError(video_id)
        return Subtitle(video_id,self._fixtures[video_id],'offline fixture')

class VideoReader:
    """Untrusted bytes treated only as data; no references to Store, Core, or tools."""
    def __init__(self,provider: ReadOnlySubtitleProvider): self._provider=provider
    def read(self,video_id):
        s=self._provider.get_subtitle(video_id)
        if not isinstance(s,Subtitle) or s.video_id!=video_id or s.coverage!='subtitle_only': raise ValueError('provider contract')
        result={'video_id':s.video_id,'untrusted_subtitle':s.text,'source':s.source,'coverage':s.coverage,'model_called':False}
        if s.metadata is not None: result['metadata']=s.metadata
        return result
