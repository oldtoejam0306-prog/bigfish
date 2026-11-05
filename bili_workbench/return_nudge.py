"""Optional in-page return reminder; no monitoring outside the browser page."""
import json
import math
import time
from .runtime import Fault, canonical


class ReturnNudge:
    def __init__(self,state):
        self.s=state
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_return_nudge (id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')
        self.s.db.execute('INSERT OR IGNORE INTO local_return_nudge VALUES (1,?)',(canonical({'enabled':False,'minimum_seconds':900,'last_prompt':0}),))

    def status(self):
        with self.s.lock:
            item=json.loads(self.s.db.execute('SELECT settings FROM local_return_nudge WHERE id=1').fetchone()[0])
        return {'enabled':item['enabled'],'minimum_seconds':item['minimum_seconds'],'cooldown_seconds':item.get('cooldown_seconds',item['minimum_seconds']),'message':item.get('message','欢迎回来，要继续聊聊吗？')}

    def configure(self,enabled,minimum_seconds,cooldown_seconds=None,message=None):
        if type(enabled) is not bool or type(minimum_seconds) is not int or not 300<=minimum_seconds<=86400:raise Fault('提醒设置无效')
        if cooldown_seconds is not None and (type(cooldown_seconds) is not int or not 300<=cooldown_seconds<=604800):raise Fault('提醒间隔无效')
        if message is not None and (not isinstance(message,str) or not message.strip() or len(message)>120 or any(ord(ch)<32 for ch in message)):raise Fault('提醒文字须为 1 至 120 个字符')
        with self.s.lock:
            current=json.loads(self.s.db.execute('SELECT settings FROM local_return_nudge WHERE id=1').fetchone()[0])
            current.update(enabled=enabled,minimum_seconds=minimum_seconds)
            if cooldown_seconds is not None:current['cooldown_seconds']=cooldown_seconds
            if message is not None:current['message']=message.strip()
            self.s.db.execute('UPDATE local_return_nudge SET settings=? WHERE id=1',(canonical(current),))
        return self.status()

    def returned(self,away_seconds,now=None):
        if type(away_seconds) not in (int,float) or not math.isfinite(away_seconds) or not 0<=away_seconds<=604800:raise Fault('离开时间无效')
        now=time.time() if now is None else now
        with self.s.lock:
            current=json.loads(self.s.db.execute('SELECT settings FROM local_return_nudge WHERE id=1').fetchone()[0])
            if not current['enabled'] or away_seconds<current['minimum_seconds'] or now-current['last_prompt']<current.get('cooldown_seconds',current['minimum_seconds']):
                return {'show':False}
            current['last_prompt']=now
            self.s.db.execute('UPDATE local_return_nudge SET settings=? WHERE id=1',(canonical(current),))
        return {'show':True,'text':current.get('message','欢迎回来，要继续聊聊吗？'),'source':'本机提醒，不是模型生成'}
