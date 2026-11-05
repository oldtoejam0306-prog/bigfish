"""OneBot v11-shaped offline fixtures. No network, QQ login, or sending."""
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from app import BigFish
from boundary import FixtureSubtitleProvider

class Rejected(ValueError): pass

@dataclass(frozen=True)
class OfflineConfig:
    bot_id: int | None = None
    owner_id: int | None = None
    paid_budget_micro: int = 0
    def validate(self):
        if self.paid_budget_micro != 0 or type(self.paid_budget_micro) is not int: raise Rejected('本阶段付费预算固定为零')
        for n in (self.bot_id,self.owner_id):
            if n is not None and (type(n) is not int or not 0<n<2**63): raise Rejected('QQ ID 必须为正整数')
        if self.bot_id is not None and self.bot_id==self.owner_id: raise Rejected('独立账号不能相同')

class FixtureEventSource:
    """Not authenticated: only test fixtures. Never expose to network or model."""
    def __init__(self,config): config.validate(); self.__config=config
    def parse(self,event):
        c=self.__config
        if c.bot_id is None or c.owner_id is None: raise Rejected('身份未配置，默认拒绝')
        if not isinstance(event,dict): raise Rejected('事件无效')
        if event.get('post_type')!='message' or event.get('message_type')!='private' or event.get('sub_type')!='friend': raise Rejected('仅允许好友私聊')
        if type(event.get('self_id')) is not int or type(event.get('user_id')) is not int or event['self_id']!=c.bot_id or event['user_id']!=c.owner_id: raise Rejected('账号不匹配')
        mid=event.get('message_id')
        if type(mid) is not int or not -(2**31)<=mid<2**31: raise Rejected('消息ID无效')
        # Message array only, so CQ-looking plain text cannot become a tool call.
        msg=event.get('message')
        if not isinstance(msg,list) or not 0<len(msg)<=100: raise Rejected('仅接受文本段数组')
        parts=[]
        for seg in msg:
            if not isinstance(seg,dict) or seg.get('type')!='text' or not isinstance(seg.get('data'),dict) or not isinstance(seg['data'].get('text'),str): raise Rejected('仅接受纯文本消息')
            parts.append(seg['data']['text'])
        content=''.join(parts)
        if not content.strip() or len(content.encode('utf-8'))>16000: raise Rejected('文本为空或过长')
        return (c.bot_id,c.owner_id,mid,content)

class MockModel:
    """No API calls, tools, or inference; response is a clearly labelled fixture."""
    def reply(self,content,personality,memories):
        return '【离线模拟回复】我听到了，我们可以一起慢慢聊。'

class OfflineQQ:
    def __init__(self,path,config,source,model):
        config.validate()
        if type(source) is not FixtureEventSource or type(model) is not MockModel: raise Rejected('仅允许离线 fixture 和模拟模型')
        # Source identity is checked against the same configuration on each event.
        self.__config=config; self.__source=source; self.__model=model
        p=FixtureSubtitleProvider({}); self.__app=BigFish(path,p,p.revision)
        self.__db=sqlite3.connect(str(path),timeout=30,isolation_level=None)
        self.__db.execute('CREATE TABLE IF NOT EXISTS qq_fixture_events(bot_id INTEGER, user_id INTEGER, message_id INTEGER, digest TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(bot_id,user_id,message_id))')
        self.__db.execute('CREATE TABLE IF NOT EXISTS qq_owner_binding(singleton INTEGER PRIMARY KEY CHECK(singleton=1), bot_id INTEGER NOT NULL, owner_id INTEGER NOT NULL)')
        if config.bot_id is not None and config.owner_id is not None:
            self.__db.execute('BEGIN IMMEDIATE')
            try:
                existing=self.__db.execute('SELECT bot_id,owner_id FROM qq_owner_binding WHERE singleton=1').fetchone()
                if existing is not None and existing!=(config.bot_id,config.owner_id): raise Rejected('此数据库已绑定其他身份；须显式处理数据迁移')
                if existing is None: self.__db.execute('INSERT INTO qq_owner_binding VALUES (1,?,?)',(config.bot_id,config.owner_id))
                self.__db.commit()
            except Exception:
                self.__db.rollback(); self.__db.close(); self.__app.close(); raise
    def close(self): self.__db.close(); self.__app.close()
    def receive(self,event):
        bot,owner,mid,content=self.__source.parse(event)
        if (bot,owner)!=(self.__config.bot_id,self.__config.owner_id): raise Rejected('配置与事件来源不匹配')
        digest=hashlib.sha256(content.encode()).hexdigest()
        self.__db.execute('BEGIN IMMEDIATE')
        try:
            row=self.__db.execute('SELECT digest,status FROM qq_fixture_events WHERE bot_id=? AND user_id=? AND message_id=?',(bot,owner,mid)).fetchone()
            if row:
                if row[0]!=digest: raise Rejected('相同消息ID的内容冲突')
                self.__db.commit(); return {'status':'duplicate_or_uncertain','send':False}
            self.__db.execute("INSERT INTO qq_fixture_events VALUES (?,?,?,?,'started')",(bot,owner,mid,digest)); self.__db.commit()
        except Exception: self.__db.rollback(); raise
        # Explicit trusted local binding: only matching configured fixture owner.
        session=self.__app.local_owner_session()
        draft=self.__model.reply(content,session.personality(),session.memories())
        self.__db.execute("UPDATE qq_fixture_events SET status='completed' WHERE bot_id=? AND user_id=? AND message_id=?",(bot,owner,mid))
        return {'status':'mock_draft','text':draft,'send':False,'model_usage':None,'paid_cost':0}
