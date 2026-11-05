"""Opt-in, rule-based simulated character tone; not emotion recognition."""
import re
import time
from .runtime import Fault


class SimulatedEmotion:
    POSITIVE=('开心','高兴','太好了','真棒','喜欢','快乐','谢谢')
    SUPPORT=('难过','伤心','失望','难受','委屈','不开心','不高兴','不喜欢','不快乐')
    def __init__(self,state):
        self.s=state
        self.s.db.execute('CREATE TABLE IF NOT EXISTS simulated_emotion (character_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL,tone TEXT NOT NULL,updated REAL NOT NULL)')

    @staticmethod
    def validate_id(character_id):
        if character_id!='default' and (not isinstance(character_id,str) or not re.fullmatch(r'[0-9a-f]{24}',character_id)):raise Fault('角色编号无效')

    def status(self,character_id,now=None):
        self.validate_id(character_id);now=time.time() if now is None else now
        with self.s.lock:row=self.s.db.execute('SELECT enabled,tone,updated FROM simulated_emotion WHERE character_id=?',(character_id,)).fetchone()
        if not row:return {'enabled':False,'tone':'跟随人设'}
        return {'enabled':bool(row['enabled']),'tone':row['tone'] if now-row['updated']<3600 else '跟随人设'}

    def configure(self,character_id,enabled):
        self.validate_id(character_id)
        if type(enabled) is not bool:raise Fault('情绪模式设置无效')
        with self.s.lock:self.s.db.execute("INSERT INTO simulated_emotion VALUES(?,?,'跟随人设',0) ON CONFLICT(character_id) DO UPDATE SET enabled=excluded.enabled,tone='跟随人设',updated=0",(character_id,int(enabled)))
        return self.status(character_id)

    def predict(self,character_id,text,now=None):
        status=self.status(character_id,now)
        if not status['enabled']:return None
        # Treat a negated positive phrase as a support cue; this is not a diagnosis.
        if any(term in text for term in self.SUPPORT):return '温柔'
        if any(term in text for term in self.POSITIVE):return '轻快'
        return status['tone']

    def record(self,character_id,tone,now=None):
        self.validate_id(character_id)
        if tone not in ('跟随人设','温柔','轻快'):raise Fault('模拟情绪无效')
        with self.s.lock:self.s.db.execute('UPDATE simulated_emotion SET tone=?,updated=? WHERE character_id=? AND enabled=1',(tone,time.time() if now is None else now,character_id))
