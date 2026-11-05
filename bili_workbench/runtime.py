import base64
import ctypes
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

class Fault(ValueError): pass

DEFAULT={'read_enabled':False,'account_mid':'','provider_url':'','model':'','vision_model':'','paid_enabled':False,'daily_budget_micro':0,'request_cap_micro':0,'input_price_micro_per_million':0,'output_price_micro_per_million':0,'price_version':'','currency':'CNY','vision_price_version':'','vision_input_price_micro_per_million':0,'vision_output_price_micro_per_million':0,'max_input_tokens':16000,'max_output_tokens':1000,'outbound_enabled':False,'dm_allowlist':[],'write_daily_limit':3,'browse_daily_limit':12,'browse_visual_fallback':False,'budget_day_timezone':'UTC','qq_endpoint':'','qq_sender_enabled':False,'qq_transport_validated':False,'qq_transport_owner_test_only':True,'qq_auto_process':False,'qq_auto_send':False}

def budget_day(config,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise Fault('预算日期须含时区')
    zone=timezone(timedelta(hours=8)) if config.get('budget_day_timezone')=='Asia/Shanghai' else timezone.utc
    return now.astimezone(zone).date().isoformat()

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x): return hashlib.sha256(canonical(x).encode()).hexdigest()

class Vault:
    def __init__(self,root): self.path=Path(root)/'secrets.bin'
    def _crypt(self,data,decrypt=False):
        if os.name!='nt': return data
        from ctypes import wintypes
        class Blob(ctypes.Structure): _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_byte))]
        buf=ctypes.create_string_buffer(data); src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte))); dst=Blob()
        crypt=ctypes.WinDLL('crypt32',use_last_error=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        fn.restype=wintypes.BOOL
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        # UI forbidden; protection binds to current Windows user.
        ctypes.set_last_error(0)
        if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst)):
            raise Fault(f'Windows凭据保护失败（错误 {ctypes.get_last_error()}）')
        try: return ctypes.string_at(dst.pbData,dst.cbData)
        finally: kernel.LocalFree(dst.pbData)
    def read(self):
        if not self.path.exists(): return {}
        try: return json.loads(self._crypt(self.path.read_bytes(),True))
        except Exception: raise Fault('无法解密本机凭据，请使用原Windows用户') from None
    def save(self,data):
        raw=self._crypt(canonical(data).encode()); temp=self.path.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(temp,self.path)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class HTTP:
    """No redirects or automatic retries; never includes response text in errors."""
    def __init__(self): self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,method,url,headers=None,body=None,limit=4_000_000):
        request=urllib.request.Request(url,data=body,headers=headers or {},method=method)
        try:
            with self.opener.open(request,timeout=30) as r:
                data=r.read(limit+1)
                if len(data)>limit: raise Fault('响应过大')
                h=dict(r.headers);h['_set_cookies']=r.headers.get_all('Set-Cookie',[])
                return data,h
        except urllib.error.HTTPError as e: raise Fault('HTTP '+str(e.code)) from None
        except (urllib.error.URLError,TimeoutError,OSError): raise Fault('网络失败或结果不确定，请勿自动重试写入') from None
    def json(self,method,url,headers=None,payload=None,form=False):
        h=dict(headers or {}); body=None
        if payload is not None:
            body=(urllib.parse.urlencode(payload) if form else canonical(payload)).encode()
            h['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
        data,_=self.request(method,url,h,body)
        try: return json.loads(data)
        except ValueError: raise Fault('服务未返回JSON') from None

class State:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.root/'workbench.db'),check_same_thread=False,isolation_level=None,timeout=30);self.db.row_factory=sqlite3.Row
        if os.name!='nt':os.chmod(self.root/'workbench.db',0o600)
        self.lock=threading.RLock(); self.vault=Vault(root)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS cfg(id INTEGER PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,value TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS charges(id TEXT PRIMARY KEY,day TEXT,reserved INTEGER,actual INTEGER,state TEXT,model TEXT);
        CREATE TABLE IF NOT EXISTS charge_meta(id TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,account TEXT,state TEXT,created REAL,result TEXT);
        CREATE TABLE IF NOT EXISTS controls(key TEXT PRIMARY KEY,value REAL);
        CREATE TABLE IF NOT EXISTS write_attempts(id TEXT PRIMARY KEY,created REAL);
        CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,content TEXT,created REAL);''')
        self.db.execute('INSERT OR IGNORE INTO cfg VALUES (1,?)',(canonical(DEFAULT),))
        # Process interruption cannot prove whether a remote action executed.
        self.db.execute("UPDATE drafts SET state='uncertain' WHERE state='sending'")
        self.db.execute("UPDATE charges SET state='uncertain' WHERE state='reserved'")
    def close(self): self.db.close()
    def config(self): return dict(DEFAULT,**json.loads(self.db.execute('SELECT value FROM cfg WHERE id=1').fetchone()[0]))
    def configure(self,values):
        c=self.config()
        if set(values)-set(DEFAULT): raise Fault('未知设置字段')
        for k,v in values.items():
            if isinstance(DEFAULT[k],bool):
                if type(v) is not bool: raise Fault(k+'须为布尔值')
            elif isinstance(DEFAULT[k],int):
                if type(v) is not int or not 0<=v<=10**12: raise Fault(k+'须为非负整数')
            elif isinstance(DEFAULT[k],list):
                if not isinstance(v,list) or len(v)>100 or any(not isinstance(x,str) or not x.isdigit() for x in v): raise Fault('白名单须为数字字符串')
            elif not isinstance(v,str) or len(v)>2000: raise Fault('设置格式无效')
            c[k]=v
        if c['qq_endpoint']:
            endpoint=urllib.parse.urlsplit(c['qq_endpoint'])
            if endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or endpoint.port is None or endpoint.path not in ('','/') or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:raise Fault('QQ端点只允许本机127.0.0.1明确端口，不自动搜索账号')
        if c['provider_url']:
            u=urllib.parse.urlsplit(c['provider_url'])
            if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment: raise Fault('提供方须为无凭据的HTTPS地址')
        if c['budget_day_timezone'] not in ('UTC','Asia/Shanghai'):raise Fault('预算时区仅支持UTC/Asia/Shanghai')
        if c['budget_day_timezone']!=self.config()['budget_day_timezone'] and self.db.execute('SELECT 1 FROM charges').fetchone():raise Fault('已有账本不能静默切换预算日边界，请先审查迁移')
        if c['currency'] not in ('CNY','USD'):raise Fault('账本币种仅支持CNY或USD')
        if c['currency']!=self.config()['currency'] and self.db.execute("SELECT 1 FROM charges WHERE day=? OR state IN ('reserved','uncertain')",(budget_day(self.config()),)).fetchone():raise Fault('本日或不确定费用存在，不能切换账本币种')
        if c['account_mid'] and not c['account_mid'].isdigit(): raise Fault('账号MID无效')
        if not 1<=c['max_output_tokens']<=8000 or not 1<=c['max_input_tokens']<=128000: raise Fault('token上限无效')
        if c['browse_daily_limit']>100:raise Fault('每日浏览最多100条')
        if c['write_daily_limit']>20: raise Fault('每日外发最多20次')
        if c['account_mid']!=self.config()['account_mid'] and self.db.execute("SELECT 1 FROM drafts WHERE state IN ('pending','approved','sending','uncertain')").fetchone(): raise Fault('有未处理草稿，不可切换账号')
        with self.lock: self.db.execute('UPDATE cfg SET value=? WHERE id=1',(canonical(c),))
        return c
    def cached(self,key):
        row=self.db.execute('SELECT value FROM cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        return json.loads(row[0]) if row else None
    def cache(self,key,value,ttl): self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(key,canonical(value),time.time()+ttl))
    def gate(self,name,interval=1,wait=False):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                now=time.time(); row=self.db.execute('SELECT value FROM controls WHERE key=?',(name,)).fetchone()
                if row and row[0]>now:
                    delay=row[0]-now
                    if wait and delay<=5:
                        self.db.rollback();time.sleep(delay);return self.gate(name,interval,wait=True)
                    raise Fault('正在限流/退避，请稍后再试')
                self.db.execute('INSERT OR REPLACE INTO controls VALUES (?,?)',(name,now+interval));self.db.commit()
            except Exception:self.db.rollback();raise
    def pause(self,name,seconds): self.db.execute('INSERT OR REPLACE INTO controls VALUES (?,?)',(name,time.time()+seconds))
    def status(self):
        c=self.config();v=self.vault.read(); day=budget_day(self.config())
        charged=self.db.execute("SELECT COALESCE(sum(CASE WHEN state='settled' THEN actual ELSE reserved END),0) FROM charges WHERE day=?",(day,)).fetchone()[0]
        return {'config':c,'key_present':bool(v.get('api_key')),'login_present':bool(v.get('cookie')),'budget_used_micro':charged,'charges':[dict(dict(x),pricing=json.loads(x['pricing']) if x['pricing'] else None) for x in self.db.execute('SELECT c.*,m.value AS pricing FROM charges c LEFT JOIN charge_meta m ON c.id=m.id ORDER BY c.rowid DESC LIMIT 100')],'drafts':[dict(x) for x in self.db.execute('SELECT id,kind,account,state,created FROM drafts ORDER BY created DESC LIMIT 100')],'missing':[label for ok,label in [(bool(c['provider_url'] and c['model']),'模型地址/模型名'),(bool(v.get('api_key')),'本机API key'),(bool(c['paid_enabled'] and c['daily_budget_micro'] and c['request_cap_micro']),'付费授权/预算'),(bool(v.get('cookie') and c['account_mid']),'独立B站账号登录')] if not ok]}
