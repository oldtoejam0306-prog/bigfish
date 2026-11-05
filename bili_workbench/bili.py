"""Bilibili endpoints and WBI adapted from MIT dsh-bili-whale pinned source.
No calls happen on import; every account and write action is explicit.
"""
import hashlib
import http.cookies
import json
import math
import re
import time
import urllib.parse
from .runtime import Fault,canonical,digest
MIXIN=[46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]
BV=re.compile(r'^BV[0-9a-zA-Z]{10}$')
API='https://api.bilibili.com'

def parse_video_reference(value):
    """Parse a BV or canonical Bilibili video URL without visiting the URL."""
    if not isinstance(value,str) or not value.strip() or len(value)>2048:raise Fault('视频链接或 BV 号无效')
    value=value.strip()
    if BV.fullmatch(value):return {'bvid':value,'part_index':None}
    try:
        parts=urllib.parse.urlsplit(value)
        hostname,port=parts.hostname,parts.port
    except ValueError:raise Fault('视频链接格式无效')
    if parts.scheme not in ('https','http') or hostname not in ('www.bilibili.com','m.bilibili.com','bilibili.com') or parts.username or parts.password or port is not None:
        raise Fault('请填写 B站视频页链接；短链接请先在浏览器中展开')
    match=re.fullmatch(r'/video/(BV[0-9a-zA-Z]{10})/?',parts.path)
    if not match:raise Fault('链接中没有有效的 BV 号')
    page=urllib.parse.parse_qs(parts.query,keep_blank_values=True).get('p',[])
    if len(page)>1 or (page and (not re.fullmatch(r'[0-9]{1,4}',page[0]) or not 1<=int(page[0])<=1000)):raise Fault('分P参数无效')
    return {'bvid':match.group(1),'part_index':int(page[0]) if page else None}

def sign_wbi(params,images,ts=None):
    raw=''.join(str(images[k]).split('/')[-1].split('.')[0] for k in ('img_url','sub_url'))
    if len(raw)!=64: raise Fault('WBI密钥缺失')
    key=''.join(raw[i] for i in MIXIN)[:32];values=dict(params,wts=int(time.time() if ts is None else ts));values.pop('w_rid',None)
    q='&'.join(urllib.parse.quote(str(k),safe='')+'='+urllib.parse.quote(re.sub(r"[!'()*]",'',str(v)),safe='') for k,v in sorted(values.items()))
    return q+'&w_rid='+hashlib.md5((q+key).encode()).hexdigest()

class Bili:
    def __init__(self,state,http):self.s=state;self.http=http;self.qr=None;self._pending_login=None
    def check_cancel(self):
        if getattr(self,'cancel_check',lambda:False)():raise Fault('任务已暂停/取消，未开始后续网络请求')
    def headers(self,cookie=None):
        return {'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/','Cookie':cookie if cookie is not None else self.s.vault.read().get('cookie','')}
    def get(self,path,params=None,signed=False,ttl=0,public=False):
        self.check_cancel()
        if not public and not self.s.config()['read_enabled']: raise Fault('请在本地配置中授权B站只读访问')
        if not public and path!='/x/web-interface/nav':self.ensure_read_identity()
        params=params or {};account=self.s.config()['account_mid'];key='bili:'+digest([account,path,params])
        if ttl:
            hit=self.s.cached(key)
            if hit is not None:return hit
        self.s.gate('bili',1,wait=True)
        if signed:
            self.check_cancel()
            nav=self.http.json('GET',API+'/x/web-interface/nav',self.headers())
            if not isinstance(nav,dict) or type(nav.get('code')) is not int or not isinstance(nav.get('data'),dict):raise Fault('WBI响应类型无效')
            if nav.get('code')==-101 and self.s.vault.read().get('cookie'):
                self._record_login('expired');raise Fault('登录已失效，未降级到匿名读取')
            if nav.get('code') not in (0,-101) or not nav.get('data',{}).get('wbi_img'):raise Fault('无法获取WBI密钥')
            query=sign_wbi(params,nav['data']['wbi_img'])
            self.s.gate('bili',1,wait=True)
        else:query=urllib.parse.urlencode(params)
        self.check_cancel()
        try:data=self.http.json('GET',API+path+('?' +query if query else ''),self.headers())
        except Fault as e:
            if str(e) in ('HTTP 412','HTTP 429','HTTP 567'): self.s.pause('bili',300)
            raise
        if not isinstance(data,dict) or type(data.get('code')) is not int or data.get('code')!=0:
            if isinstance(data,dict) and data.get('code')==-101 and self.s.vault.read().get('cookie'):self._record_login('expired')
            if isinstance(data,dict) and data.get('code') in (-412,-352,-799,-509,-10403):self.s.pause('bili',300)
            code=data.get('code') if isinstance(data,dict) else None
            raise Fault('B站返回错误码 '+(str(code) if type(code) is int and -(2**31)<=code<2**31 else 'invalid'))
        result=data.get('data')
        if not isinstance(result,dict):raise Fault('B站数据体须为对象')
        if ttl:self.s.cache(key,result,ttl)
        return result
    def identity_key(self):
        return digest([self.s.config()['account_mid'],self.s.vault.read().get('cookie','')])
    def _record_login(self,state,reason=None):
        value={'state':state,'credential_present':True,'verified':state=='valid','checked_at':time.time(),'fresh_for_seconds':300,'anonymous_read_possible':False}
        if reason:value['reason']=reason
        self.s.cache('login-status:'+self.identity_key(),value,300);return value
    def login_status(self,probe=False):
        if type(probe) is not bool:raise Fault('账号检查标识无效')
        c=self.s.config();present=bool(self.s.vault.read().get('cookie'));key='login-status:'+self.identity_key()
        if not present:return {'state':'not_configured','credential_present':False,'verified':False,'anonymous_read_possible':True}
        previous=self.s.cached(key)
        if not probe:return previous or {'state':'unchecked','credential_present':True,'verified':False}
        if not c['read_enabled']:raise Fault('先授权只读访问再检查登录态')
        self.check_cancel();self.s.gate('bili',1,wait=True);self.check_cancel()
        try:
            result=self.http.json('GET',API+'/x/web-interface/nav',self.headers())
            data=result.get('data') if isinstance(result,dict) else None
            if not isinstance(result,dict) or type(result.get('code')) is not int:raise Fault('账号响应无效')
            if result['code']==-101 or (result['code']==0 and isinstance(data,dict) and data.get('isLogin') is False):state='expired'
            elif result['code']==0 and isinstance(data,dict) and data.get('isLogin') is True and type(data.get('mid')) is int and data['mid']>0:
                state='valid' if c['account_mid'] and str(data['mid'])==c['account_mid'] else 'account_mismatch'
            else:state='unknown'
        except Fault as e:
            message=str(e)
            if message in ('HTTP 412','HTTP 429','HTTP 567'):self.s.pause('bili',300)
            state='unknown'
            reason=message if message in ('HTTP 412','HTTP 429','HTTP 567') else ('network_error' if message=='网络失败或结果不确定，请勿自动重试写入' else 'response_unavailable')
            return self._record_login(state,reason)
        return self._record_login(state)
    def ensure_read_identity(self):
        status=self.login_status()
        if status['credential_present'] and status['state']=='unchecked':status=self.login_status(True)
        if status['credential_present'] and not status['verified']:
            labels={'network_error':'账号核验时网络连接失败','response_unavailable':'账号核验响应不可用','HTTP 412':'B站返回 HTTP 412','HTTP 429':'B站返回 HTTP 429','HTTP 567':'B站返回 HTTP 567'}
            detail=labels.get(status.get('reason'),'独立账号状态为'+status['state'])
            raise Fault(detail+'；暂停读取，不降级到其他账号')
        return status
    def nav(self): return self.get('/x/web-interface/nav')
    def info(self,bvid):
        if not isinstance(bvid,str) or not BV.fullmatch(bvid):raise Fault('BV号无效')
        from .validation import view
        key='bili:'+digest([self.s.config()['account_mid'],'/x/web-interface/wbi/view',{'bvid':bvid}])
        raw=self.get('/x/web-interface/wbi/view',{'bvid':bvid},True,3600)
        try:safe=view(raw,bvid)
        except Fault:
            self.s.db.execute('DELETE FROM cache WHERE key=?',(key,));raise
        # Replace only the value; a cache hit must not extend its original TTL.
        if raw!=safe:self.s.db.execute('UPDATE cache SET value=? WHERE key=?',(canonical(safe),key))
        return safe
    def comments(self,bvid,page=1):
        if type(page) is not int or not 1<=page<=5:raise Fault('页码须为1至5')
        info=self.info(bvid)
        return self.get('/x/v2/reply',{'type':1,'oid':info['aid'],'sort':2,'ps':20,'pn':page})
    def feed(self): return self.get('/x/web-interface/popular',{'pn':1,'ps':10},ttl=300)
    def search(self,keyword):
        if not isinstance(keyword,str) or not 1<=len(keyword)<=80:raise Fault('关键词长度无效')
        return self.get('/x/web-interface/wbi/search/type',{'search_type':'video','keyword':keyword,'page':1},True,300)
    def subtitle(self,bvid,refresh=False,*,part_index=1):
        if type(refresh) is not bool:raise Fault('刷新标识无效')
        if type(part_index) is not int or not 1<=part_index<=1000:raise Fault('分P序号无效')
        self.ensure_read_identity()
        info=self.info(bvid);pages=info.get('pages') or []
        total=len(pages) if pages else 1
        if part_index>total:raise Fault('分P序号超出视频范围')
        cid=pages[part_index-1]['cid'] if pages else info['cid']
        cachekey='subtitle-v2:'+digest([self.identity_key(),bvid,cid] if part_index==1 else [self.identity_key(),bvid,cid,part_index])
        if not refresh:
            cached=self.s.cached(cachekey)
            if cached is not None:return dict(cached,cached=True)
        player=self.get('/x/player/wbi/v2',{'bvid':bvid,'cid':cid},True)
        subtitle=player.get('subtitle')
        if subtitle is None: subtitle={}
        if not isinstance(subtitle,dict):raise Fault('字幕轨道格式无效')
        tracks=subtitle.get('subtitles',[])
        if not isinstance(tracks,list) or len(tracks)>100:raise Fault('字幕轨道格式无效')
        part={'cid':cid,'part_index':part_index,'parts_total':total}
        if not tracks:
            result={'bvid':bvid,'title':info['title'],'text':'','segments':[],'coverage':'无字幕','full_video_watched':False,'reason':'未提供字幕；AI字幕可能需要独立账号登录'}
            result.update(part);self.s.cache(cachekey,result,300);return result
        if any(not isinstance(x,dict) for x in tracks):raise Fault('字幕轨道格式无效')
        pick=next((x for x in tracks if x.get('lan') in ('ai-zh','zh-CN','zh')),tracks[0]);url=pick.get('subtitle_url','')
        if not isinstance(url,str) or not url or len(url)>4096:raise Fault('字幕地址无效')
        if url.startswith('//'):url='https:'+url
        try:
            u=urllib.parse.urlsplit(url);port=u.port
        except ValueError:raise Fault('字幕地址无效') from None
        if u.scheme!='https' or not u.hostname or not (u.hostname.endswith('.hdslb.com') or u.hostname.endswith('.bilibili.com')) or u.username or u.password or port not in (None,443):raise Fault('字幕地址未获允许')
        self.s.gate('bili',1,wait=True)
        self.check_cancel()
        data=self.http.json('GET',url,{'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'})
        if not isinstance(data,dict):raise Fault('字幕格式无效')
        segments=data.get('body')
        if not isinstance(segments,list) or len(segments)>20000:raise Fault('字幕格式无效')
        clean=[]
        for x in segments:
            from .validation import subtitle_segment
            clean.append(subtitle_segment(x))
        text='\n'.join(x['content'] for x in clean)
        if len(text.encode())>1_000_000:raise Fault('字幕过长')
        result={'bvid':bvid,'title':info['title'],'text':text,'segments':clean,'language':pick.get('lan'),'coverage':'仅字幕','source':'B站字幕API','full_video_watched':False}
        result.update(part)
        result['content_revision']=digest([cid,pick.get('lan'),clean]);self.s.cache(cachekey,result,300)
        self.s.db.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?)',('bili:'+bvid+('' if part_index==1 else ':p'+str(part_index)),canonical(result),time.time()))
        return result
    def media(self,bvid,*,part_index=1,expected_cid=None):
        if type(part_index) is not int or not 1<=part_index<=1000:raise Fault('分P序号无效')
        info=self.info(bvid);pages=info.get('pages') or [];total=len(pages) if pages else 1
        if part_index>total:raise Fault('分P序号超出视频范围')
        cid=pages[part_index-1]['cid'] if pages else info['cid']
        if expected_cid is not None and (type(expected_cid) is not int or cid!=expected_cid):raise Fault('视频分P已变化，请重新读取后再试')
        data=self.get('/x/player/wbi/playurl',{'bvid':bvid,'cid':cid,'qn':32,'fnval':1,'fourk':0},True)
        urls=data.get('durl')
        if isinstance(urls,list) and len(urls)==1 and isinstance(urls[0],dict):
            item=urls[0];size=item.get('size');url=item.get('url','')
            if type(size) is not int or not 0<size<=50_000_000:raise Fault('媒体超过50MB或大小未知；请明确提供本地视频')
        else:
            dash=data.get('dash')
            if not isinstance(dash,dict):
                data=self.get('/x/player/wbi/playurl',{'bvid':bvid,'cid':cid,'qn':32,'fnval':16,'fourk':0},True)
                dash=data.get('dash')
            if not isinstance(dash,dict) or not isinstance(dash.get('video'),list):raise Fault('未获得受支持的单段MP4/DASH；不绕过权限')
            duration=dash.get('duration',data.get('timelength',0)/1000 if type(data.get('timelength')) in (int,float) else 0)
            if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=86400:raise Fault('DASH时长无效')
            candidates=[]
            for item in dash['video'][:100]:
                if not isinstance(item,dict):continue
                bandwidth=item.get('bandwidth');codec=item.get('codecs','')
                if type(bandwidth) is int and bandwidth>0 and isinstance(codec,str) and codec.startswith('avc1') and bandwidth*duration/8<=45_000_000:candidates.append(item)
            if not candidates:raise Fault('无低成本AVC画面轨道或预计超过45MB；请提供本地视频')
            item=min(candidates,key=lambda x:x['bandwidth']);url=item.get('baseUrl',item.get('base_url',''))
        if not isinstance(url,str):raise Fault('媒体地址无效')
        u=urllib.parse.urlsplit(url)
        if u.scheme!='https' or not u.hostname or not (u.hostname.endswith('.bilivideo.com') or u.hostname.endswith('.bilivideo.cn')) or u.username or u.password or u.port not in (None,443):raise Fault('媒体地址不在允许的HTTPS CDN')
        self.s.gate('bili',1,wait=True);self.check_cancel()
        raw,_=self.http.request('GET',url,{'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'},limit=50_000_000)
        return raw
    def login_start(self):
        if not self.s.config()['account_mid']:raise Fault('请先在设置中保存 B站账号 UID，再生成二维码')
        if self._pending_login and self._pending_login['expires']>time.time():raise Fault('已有待核验登录，请点击检查扫码状态')
        self._pending_login=None
        if self.qr and self.qr['expires']>time.time(): raise Fault('已有待完成登录，请等待过期')
        self.s.gate('login',3)
        data=self.http.json('GET','https://passport.bilibili.com/x/passport-login/web/qrcode/generate',self.headers(''))
        if not isinstance(data,dict) or type(data.get('code')) is not int or data['code']!=0 or not isinstance(data.get('data'),dict):raise Fault('获取扫码链接失败')
        d=data['data'];url=d.get('url','');u=urllib.parse.urlsplit(url)
        if not isinstance(url,str) or len(url)>4000 or u.scheme!='https' or u.hostname not in ('passport.bilibili.com','account.bilibili.com') or u.username or u.password or u.port not in (None,443) or u.fragment:raise Fault('扫码地址无效')
        if not isinstance(d.get('qrcode_key'),str) or not re.fullmatch('[A-Za-z0-9_-]{1,256}',d['qrcode_key']):raise Fault('扫码会话标识无效')
        self.qr={'key':d['qrcode_key'],'expires':time.time()+180}
        from vendor.qrcodegen import QrCode
        qr=QrCode.encode_text(url,QrCode.Ecc.MEDIUM);size=qr.get_size();parts=[]
        for y in range(size):
            for x in range(size):
                if qr.get_module(x,y):parts.append(f'M{x+4},{y+4}h1v1h-1z')
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size+8} {size+8}"><rect width="100%" height="100%" fill="white"/><path d="'+''.join(parts)+'" fill="black"/></svg>'
        return {'url':url,'qr_svg':svg,'note':'仅用大肥鱼独立账号手机扫码；点击检查状态保存已验证的登录态'}
    def _finish_pending_login(self):
        pending=self._pending_login
        if not pending or pending['expires']<=time.time():
            self._pending_login=None;self.qr=None;raise Fault('待核验登录已过期，请重新扫码')
        if pending['account_mid']!=self.s.config()['account_mid']:
            self._pending_login=None;self.qr=None;raise Fault('B站 UID 已变化，请重新扫码')
        try:
            self.check_cancel()
            result=self.save_cookie(pending['cookie'])
        except Fault as e:
            if str(e)=='网络失败或结果不确定，请勿自动重试写入':
                return {'logged_in':False,'state':'verification_pending','note':'已收到扫码确认，但账号核验网络失败；登录信息仅暂存于本进程内存，请检查网络后手动点击检查扫码状态。'}
            self._pending_login=None;self.qr=None;raise
        self._pending_login=None;return result
    def login_poll(self):
        if self._pending_login:return self._finish_pending_login()
        if not self.qr or self.qr['expires']<time.time():raise Fault('请重新开始登录')
        self.s.gate('login',3)
        data,headers=self.http.request('GET','https://passport.bilibili.com/x/passport-login/web/qrcode/poll?'+urllib.parse.urlencode({'qrcode_key':self.qr['key']}),self.headers(''))
        try:body=json.loads(data)
        except ValueError:raise Fault('登录响应格式无效')
        if not isinstance(body,dict) or type(body.get('code')) is not int or body['code']!=0 or not isinstance(body.get('data'),dict):raise Fault('登录服务错误')
        code=body['data'].get('code')
        if type(code) is not int:raise Fault('扫码状态类型无效')
        if code!=0:
            if code==86038:self.qr=None
            return {'logged_in':False,'code':code}
        cookies=http.cookies.SimpleCookie()
        for raw in headers.get('_set_cookies',[]): cookies.load(raw)
        cookie='; '.join(k+'='+v.value for k,v in cookies.items() if k in ('SESSDATA','bili_jct','buvid3','DedeUserID'))
        self._pending_login={'cookie':cookie,'account_mid':self.s.config()['account_mid'],'expires':time.time()+180}
        return self._finish_pending_login()
    def save_cookie(self,cookie):
        if not isinstance(cookie,str) or len(cookie)>16000 or any(ord(c)<32 for c in cookie):raise Fault('Cookie格式无效')
        parsed=http.cookies.SimpleCookie();parsed.load(cookie)
        if not all(k in parsed for k in ('SESSDATA','bili_jct')):raise Fault('Cookie缺少登录字段')
        expected=self.s.config()['account_mid']
        if not expected:raise Fault('请先填写大肥鱼独立B站账号MID以防登录错账号')
        self.s.gate('bili',1,wait=True)
        nav=self.http.json('GET',API+'/x/web-interface/nav',self.headers(cookie))
        if not isinstance(nav,dict) or type(nav.get('code')) is not int or nav['code']!=0 or not isinstance(nav.get('data'),dict) or nav['data'].get('isLogin') is not True or type(nav['data'].get('mid')) is not int or str(nav['data']['mid'])!=expected:raise Fault('登录账号不匹配，未保存')
        if self.s.db.execute("SELECT 1 FROM drafts WHERE state IN ('approved','sending','uncertain')").fetchone():raise Fault('有未完成外发，不能替换登录态')
        v=self.s.vault.read();v['cookie']=cookie;v['csrf']=parsed['bili_jct'].value;self.s.vault.save(v);self.qr=None
        return {'logged_in':True,'mid':expected}
    def send(self,kind,payload,cancel=None,progress=None):
        c=self.s.config();vault=self.s.vault.read()
        if not c['outbound_enabled'] or not vault.get('cookie'):raise Fault('未配置登录或未启用外发')
        me=self.nav()
        if not me.get('isLogin') or str(me.get('mid'))!=c['account_mid']:raise Fault('独立账号校验失败')
        csrf=vault.get('csrf');base={'csrf':csrf}
        if kind=='comment':
            i=self.info(payload['bvid']);base.update({'oid':i['aid'],'type':1,'message':payload['text'],'root':payload.get('root','0'),'parent':payload.get('parent','0')});url=API+'/x/v2/reply/add'
        elif kind=='dm':
            if payload['recipient'] not in c['dm_allowlist']:raise Fault('私信收件人不在显式白名单')
            base.update({'msg[sender_uid]':c['account_mid'],'msg[receiver_id]':payload['recipient'],'msg[receiver_type]':1,'msg[msg_type]':1,'msg[msg_status]':0,'msg[timestamp]':int(time.time()),'msg[content]':canonical({'content':payload['text']}),'msg[dev_id]':digest([c['account_mid'],'bigfish'])[:32]});url='https://api.vc.bilibili.com/web_im/v1/web_im/send_msg'
        elif kind=='video':
            from .upload import Uploader
            return Uploader(self,cancel,progress).run(payload)
        else:raise Fault('外发类型不支持')
        self.s.gate('bili',1,wait=True)
        if cancel and cancel():raise Fault('已取消，未发送写入请求')
        result=self.http.json('POST',url,self.headers(),base,True)
        if not isinstance(result,dict) or type(result.get('code')) is not int or result['code']!=0:raise Fault('写入回执无效或被平台拒绝，结果需人工核对')
        data=result.get('data')
        if not isinstance(data,dict):raise Fault('写入缺少可追溯消息ID，结果未知')
        value=data.get('rpid_str',data.get('rpid')) if kind=='comment' else data.get('msg_key')
        if type(value) is int and value>0:value=str(value)
        if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',value) or (kind=='comment' and not value.isdigit()):raise Fault('写入消息ID无效，结果未知')
        if any(isinstance(secret,str) and len(secret)>=8 and secret in value for secret in vault.values()):raise Fault('回执疑似包含凭据，拒绝保存正文')
        return {'code':0,'receipt':{'rpid' if kind=='comment' else 'msg_key':value},'delivery_confirmed':False}
