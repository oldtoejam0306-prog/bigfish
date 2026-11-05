"""OneBot v11 HTTP transport candidate; no listener or network call on import.

Protocol reference (independently implemented, no upstream code copied):
https://github.com/botuniverse/onebot-11/tree/d4456ee706f9ada9c2dfde56a2bcfc69752600e4
communication/http.md, http-post.md, authorization.md and api/public.md.
Compatibility with the user's actual SnowLuma bridge remains UNVERIFIED.
Secrets are supplied by trusted local configuration, never generated/read here.
"""
from dataclasses import dataclass,field
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit
import urllib.request
from .runtime import Fault,HTTP,NoRedirect,canonical,digest
from .social import validate_reply


def _id(value):return type(value) is int and 0<value<2**63

def _no_duplicates(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise Fault('事件JSON存在重复字段')
        value[key]=item
    return value

@dataclass(frozen=True)
class TransportConfig:
    endpoint:str=''
    bot_id:int|None=None
    owner_id:int|None=None
    access_token:str=field(default='',repr=False)
    event_secret:str=field(default='',repr=False)
    enabled:bool=False
    transport_validated:bool=False
    owner_test_only:bool=True
    daily_limit:int=20
    peer_daily_limit:int=5
    interval_s:int=3
    event_max_age_s:int=300
    owner_media_enabled:bool=False
    media_validated:bool=False
    def validate(self):
        if not isinstance(self.endpoint,str):raise Fault('OneBot地址无效')
        u=urlsplit(self.endpoint)
        try:port=u.port
        except ValueError:raise Fault('OneBot端口无效') from None
        if u.scheme!='http' or u.hostname!='127.0.0.1' or type(port) is not int or not 1<=port<=65535 or u.path not in ('','/') or u.username or u.password or u.query or u.fragment:raise Fault('OneBot仅允许明确的127.0.0.1 HTTP根地址与端口')
        if not _id(self.bot_id) or not _id(self.owner_id) or self.bot_id==self.owner_id:raise Fault('须明确独立机器人和主人身份')
        for value in (self.access_token,self.event_secret):
            if not isinstance(value,str) or not 16<=len(value)<=16000 or any(ord(char)<33 or ord(char)>126 for char in value):raise Fault('须在本机提供有效传输凭据，不使用空白或默认凭据')
        if any(type(x) is not bool for x in (self.enabled,self.transport_validated,self.owner_test_only,self.owner_media_enabled,self.media_validated)):raise Fault('传输开关无效')
        if any(type(x) is not int for x in (self.daily_limit,self.peer_daily_limit,self.interval_s,self.event_max_age_s)):raise Fault('传输次数/时间须为整数')
        if not 0<=self.daily_limit<=100 or not 0<=self.peer_daily_limit<=100 or not 1<=self.interval_s<=3600 or not 1<=self.event_max_age_s<=3600:raise Fault('传输次数/时间范围无效')

@dataclass(frozen=True)
class TrustedEvent:
    """Opaque evidence from this adapter's HMAC verifier, not a QQ-server proof.
    A holder can obtain a fresh dict, so subsequent caller mutations cannot alter
    the recipient identity captured in this object. Only the issuing adapter may
    use it to bind outgoing replies. HMAC does not sandbox trusted local code.
    """
    key:str
    kind:str
    peer:int
    sender:int
    replayed:bool
    _event_json:str=field(repr=False)
    _capability:object=field(repr=False,compare=False)
    def event(self):return json.loads(self._event_json)

class OneBotHTTP:
    def __init__(self,state,config,http=None,permission=None,clock=None):
        if type(config) is not TransportConfig:raise Fault('需要本机可信传输配置')
        config.validate();self.s=state;self.c=config;self.http=http or HTTP();self.permission=permission;self.clock=clock or time.time;self.__capability=object()
        if http is None:
            # Local bridge credentials must never travel through an environment proxy.
            self.http.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with self.s.lock:
            self.s.db.executescript('''CREATE TABLE IF NOT EXISTS qq_transport_events(id TEXT PRIMARY KEY,fingerprint TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS qq_transport_outbox(id TEXT PRIMARY KEY,fingerprint TEXT,action TEXT,params TEXT,peer INTEGER,scope TEXT,state TEXT,receipt TEXT,created REAL,attempted REAL);
            CREATE TABLE IF NOT EXISTS qq_transport_attempts(id TEXT PRIMARY KEY,peer INTEGER,scope TEXT,created REAL);''')
            self.s.db.execute("UPDATE qq_transport_outbox SET state='uncertain' WHERE state='sending'")
    def status(self):
        return {'implementation':'OneBot v11 HTTP candidate','endpoint':self.c.endpoint,'sender_enabled':self.c.enabled,'transport_validated_by_owner':self.c.transport_validated,'owner_test_only':self.c.owner_test_only,'actual_snowluma_compatibility':'not verified','listener_started':False,'quick_operations_used':False}
    def _allowed(self,kind,peer):
        if self.c.owner_test_only:
            if kind!='friend' or peer!=self.c.owner_id:raise Fault('本次只允许主人私聊验收')
        elif self.permission is None or self.permission(kind,peer) is not True:raise Fault('社交范围未由可信本机策略授权')
    def verify_event(self,body,headers):
        if not isinstance(body,bytes) or not 0<len(body)<=65536 or not isinstance(headers,dict):raise Fault('事件大小/请求头无效')
        lowered={}
        for key,value in headers.items():
            normalized=str(key).lower()
            if normalized in lowered:raise Fault('事件请求头存在重复字段')
            lowered[normalized]=value
        signature=lowered.get('x-signature','')
        if not isinstance(signature,str) or not re.fullmatch('sha1=[0-9a-f]{40}',signature):raise Fault('事件缺少有效签名')
        expected='sha1='+hmac.new(self.c.event_secret.encode(),body,hashlib.sha1).hexdigest()
        if not hmac.compare_digest(signature,expected):raise Fault('事件签名不匹配')
        content_type=lowered.get('content-type','')
        if not isinstance(content_type,str) or content_type.split(';')[0].strip().lower()!='application/json':raise Fault('事件类型须为JSON')
        try:event=json.loads(body,object_pairs_hook=_no_duplicates)
        except (ValueError,UnicodeError):raise Fault('事件JSON无效') from None
        if not isinstance(event,dict) or event.get('post_type')!='message' or type(event.get('self_id')) is not int or event['self_id']!=self.c.bot_id or lowered.get('x-self-id')!=str(self.c.bot_id):raise Fault('事件机器人身份不匹配')
        stamp=event.get('time');sender=event.get('user_id');mid=event.get('message_id')
        if type(stamp) is not int or abs(self.clock()-stamp)>self.c.event_max_age_s:raise Fault('事件过期或时间不可信，未处理')
        if not _id(sender) or sender==self.c.bot_id or type(mid) is not int or not -(2**31)<=mid<2**31:raise Fault('事件用户/消息标识无效')
        if event.get('message_type')=='private' and event.get('sub_type')=='friend':kind='friend';peer=sender
        elif event.get('message_type')=='group' and event.get('sub_type')=='normal' and _id(event.get('group_id')):kind='group';peer=event['group_id']
        else:raise Fault('当前仅接受好友私聊/普通群消息')
        self._allowed(kind,peer)
        message=event.get('message')
        if isinstance(message,str):message=[{'type':'text','data':{'text':message}}]
        if not isinstance(message,list) or not 0<len(message)<=100:raise Fault('消息段无效')
        segments=[];size=0
        for segment in message:
            if not isinstance(segment,dict) or not isinstance(segment.get('data'),dict):raise Fault('消息段无效')
            value=segment['data'];typ=segment.get('type')
            if typ=='text' and isinstance(value.get('text'),str):
                text=value['text'];size+=len(text.encode())
                if re.search(r'\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})\b',text):raise Fault('聊天疑似含凭据；请只在本机安全配置框填写，不入库或转模型')
                if self.c.access_token in text or self.c.event_secret in text:raise Fault('消息疑似包含传输凭据，拒绝转入模型')
                segments.append({'type':'text','data':{'text':text}})
            elif typ=='at' and kind=='group' and isinstance(value.get('qq'),(str,int)) and not isinstance(value.get('qq'),bool):segments.append({'type':'at','data':{'qq':str(value['qq'])}})
            else:raise Fault('当前不读取文件/图片/语音事件')
        if not 0<size<=16000:raise Fault('消息文本为空或过长')
        clean={'time':stamp,'self_id':self.c.bot_id,'post_type':'message','message_type':'private' if kind=='friend' else 'group','sub_type':'friend' if kind=='friend' else 'normal','user_id':sender,'message_id':mid,'message':segments}
        if kind=='group':clean['group_id']=peer
        key='onebot:'+digest([self.c.bot_id,kind,peer,sender,mid]);fingerprint=digest([kind,peer,sender,segments])
        with self.s.lock:
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                old=self.s.db.execute('SELECT fingerprint FROM qq_transport_events WHERE id=?',(key,)).fetchone()
                if old and old['fingerprint']!=fingerprint:raise Fault('相同事件ID内容冲突')
                self.s.db.execute('INSERT OR IGNORE INTO qq_transport_events VALUES (?,?,?)',(key,fingerprint,self.clock()));self.s.db.commit()
            except Exception:self.s.db.rollback();raise
        return TrustedEvent(key,kind,peer,sender,bool(old),canonical(clean),self.__capability)
    def event_dict(self,event):
        if type(event) is not TrustedEvent or event._capability is not self.__capability:raise Fault('事件不是本实例已验签来源')
        return event.event()
    def prepare(self,candidate,origin=None,*,local_authorization=False):
        if type(local_authorization) is not bool:raise Fault('本机授权标识无效')
        if not isinstance(candidate,dict) or set(candidate)-{'action','params','echo'}:raise Fault('回复候选字段无效')
        action=candidate.get('action');params=candidate.get('params');ident=candidate.get('echo')
        if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9:_-]{1,160}',ident) or action not in ('send_private_msg','send_group_msg') or not isinstance(params,dict):raise Fault('仅支持有稳定ID的文本发送候选')
        kind='friend' if action=='send_private_msg' else 'group';target='user_id' if kind=='friend' else 'group_id';peer=params.get(target)
        if set(params)-{target,'message','auto_escape'} or not _id(peer):raise Fault('收件人或参数无效')
        self._allowed(kind,peer)
        if origin is not None:
            self.event_dict(origin)
            if (kind,peer)!=(origin.kind,origin.peer):raise Fault('回复目的地与已验签会话不一致')
        elif not local_authorization:raise Fault('需要已验签事件或可信本机逐条授权')
        segments=params.get('message')
        if not isinstance(segments,list) or not 0<len(segments)<=20:raise Fault('仅允许文本段数组')
        parts=[];media=[]
        for segment in segments:
            if not isinstance(segment,dict) or set(segment)!={'type','data'} or not isinstance(segment.get('data'),dict):raise Fault('消息段无效')
            if segment.get('type')=='text' and set(segment['data'])=={'text'} and isinstance(segment['data']['text'],str):parts.append(segment['data']['text'])
            elif segment.get('type') in ('image','record'):
                if not (kind=='friend' and peer==self.c.owner_id and self.c.owner_media_enabled and self.c.media_validated):raise Fault('媒体仅限已单独验收的主人私聊')
                value=segment['data'].get('file')
                if set(segment['data'])!={'file'} or not isinstance(value,str) or not value.startswith('base64://') or any(x['type']==segment['type'] for x in media):raise Fault('仅支持单张本机图片与单条语音，不读取路径或URL')
                from .owner_expression import validate_binary
                validate_binary(segment['type'],value[9:]);media.append({'type':segment['type'],'data':{'file':value}})
            else:raise Fault('不解释CQ、工具或其他附件消息段')
        text=''.join(parts)
        if self.c.access_token in text or self.c.event_secret in text:raise Fault('草稿疑似包含传输凭据')
        validate_reply(text,public=False)
        if len(text.encode())>6000:raise Fault('回复草稿过长')
        clean={target:peer,'message':[{'type':'text','data':{'text':text}}]+media}
        fingerprint=digest([self.c.endpoint,self.c.bot_id,action,clean])
        with self.s.lock:
            old=self.s.db.execute('SELECT fingerprint,state FROM qq_transport_outbox WHERE id=?',(ident,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Fault('发送ID内容冲突')
                return {'id':ident,'state':old['state'],'network_sent':False,'replayed':True}
            self.s.db.execute("INSERT INTO qq_transport_outbox VALUES (?,?,?,?,?,?,'prepared',NULL,?,NULL)",(ident,fingerprint,action,canonical(clean),peer,kind,self.clock()))
        return {'id':ident,'state':'prepared','network_sent':False,'dry_run':True}
    def inspect(self,ident):
        with self.s.lock:row=self.s.db.execute('SELECT id,action,params,peer,scope,state,receipt,created,attempted FROM qq_transport_outbox WHERE id=?',(ident,)).fetchone()
        if not row:raise Fault('发送草稿不存在')
        value=dict(row);value['params']=json.loads(value['params']);value['receipt']=json.loads(value['receipt']) if value['receipt'] else None;return value
    def send(self,ident,confirmation,*,cancel_check=None):
        if not self.c.enabled or not self.c.transport_validated:raise Fault('发送关闭或真实传输尚未由本机验收')
        if confirmation!='确认发送':raise Fault('需要明确逐次发送授权')
        if cancel_check and cancel_check():raise Fault('已取消，未调用传输')
        with self.s.lock:
            self.s.gate('qq-transport-send',self.c.interval_s)
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.s.db.execute('SELECT * FROM qq_transport_outbox WHERE id=?',(ident,)).fetchone()
                if not row or row['state']!='prepared':raise Fault('草稿未准备、已执行或结果未知，不重放')
                if row['fingerprint']!=digest([self.c.endpoint,self.c.bot_id,row['action'],json.loads(row['params'])]):raise Fault('草稿绑定的传输地址/机器人已改变，必须重新审查')
                self._allowed(row['scope'],row['peer'])
                if any(x.get('type')!='text' for x in json.loads(row['params'])['message']) and not (row['scope']=='friend' and row['peer']==self.c.owner_id and self.c.owner_media_enabled and self.c.media_validated):raise Fault('主人媒体权限已改变，未调用发送API')
                since=self.clock()-86400
                count=self.s.db.execute('SELECT count(*) FROM qq_transport_attempts WHERE created>=?',(since,)).fetchone()[0]
                peers=self.s.db.execute('SELECT count(*) FROM qq_transport_attempts WHERE created>=? AND peer=? AND scope=?',(since,row['peer'],row['scope'])).fetchone()[0]
                if count>=self.c.daily_limit or peers>=self.c.peer_daily_limit:raise Fault('发送尝试次数已到上限')
                self.s.db.execute('INSERT INTO qq_transport_attempts VALUES (?,?,?,?)',(ident,row['peer'],row['scope'],self.clock()))
                self.s.db.execute("UPDATE qq_transport_outbox SET state='sending',attempted=? WHERE id=?",(self.clock(),ident));self.s.db.commit()
            except Exception:self.s.db.rollback();raise
        headers={'Authorization':'Bearer '+self.c.access_token};endpoint=self.c.endpoint.rstrip('/')
        try:
            identity=self.http.json('POST',endpoint+'/get_login_info',headers,{})
            if not isinstance(identity,dict) or identity.get('status')!='ok' or type(identity.get('retcode')) is not int or identity['retcode']!=0 or not isinstance(identity.get('data'),dict) or type(identity['data'].get('user_id')) is not int or identity['data']['user_id']!=self.c.bot_id:
                self.s.db.execute("UPDATE qq_transport_outbox SET state='rejected_identity' WHERE id=?",(ident,));raise Fault('登录机器人身份未确认，未调用发送API')
            if cancel_check and cancel_check():
                self.s.db.execute("UPDATE qq_transport_outbox SET state='cancelled_not_sent' WHERE id=?",(ident,));raise Fault('已取消，未调用发送API')
            receipt=self.http.json('POST',endpoint+'/'+row['action'],headers,json.loads(row['params']))
            if not isinstance(receipt,dict) or receipt.get('status')!='ok' or type(receipt.get('retcode')) is not int or receipt['retcode']!=0 or ('echo' in receipt and receipt['echo']!=ident):raise Fault('发送回执未知')
            data=receipt.get('data');mid=data.get('message_id') if isinstance(data,dict) else None
            if type(mid) is not int or not -(2**31)<=mid<2**31:raise Fault('发送回执消息ID无效')
            result={'api_acknowledged':True,'message_id':mid,'delivery_confirmed':False,'note':'OneBot API回执，不代表QQ服务器/对方收信已独立验收'}
            self.s.db.execute("UPDATE qq_transport_outbox SET state='acknowledged',receipt=? WHERE id=?",(canonical(result),ident));return result
        except Exception:
            current=self.s.db.execute('SELECT state FROM qq_transport_outbox WHERE id=?',(ident,)).fetchone()[0]
            if current=='sending':self.s.db.execute("UPDATE qq_transport_outbox SET state='uncertain' WHERE id=?",(ident,))
            raise Fault('发送未完成或回执未知；未自动重发。身份/取消状态可在本机草稿中查看') from None
