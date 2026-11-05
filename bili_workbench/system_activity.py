"""Opt-in session idle detection and coarse, local-only activity aggregation."""
from collections import deque
from datetime import datetime,timedelta,timezone
import ctypes
from ctypes import wintypes
import os
import threading
import time
import uuid
from .growth import Scope
from .runtime import Fault


TZ=timezone(timedelta(hours=8))
CATEGORIES=('浏览与交流','工作','创作','其他','无法识别')


def windows_idle_seconds():
    if os.name!='nt':raise Fault('系统空闲检测仅支持 Windows')
    class LASTINPUTINFO(ctypes.Structure):
        _fields_=[('cbSize',wintypes.UINT),('dwTime',wintypes.DWORD)]
    user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.GetLastInputInfo.argtypes=[ctypes.POINTER(LASTINPUTINFO)];user.GetLastInputInfo.restype=wintypes.BOOL
    kernel.GetTickCount64.restype=ctypes.c_ulonglong
    info=LASTINPUTINFO();info.cbSize=ctypes.sizeof(info)
    if not user.GetLastInputInfo(ctypes.byref(info)):raise Fault('系统未提供空闲时间')
    return ((kernel.GetTickCount64()&0xffffffff)-info.dwTime)&0xffffffff


def windows_foreground_category():
    if os.name!='nt':raise Fault('电脑活动统计仅支持 Windows')
    user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    user.GetForegroundWindow.restype=wintypes.HWND
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)];kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    hwnd=user.GetForegroundWindow();pid=wintypes.DWORD()
    if not hwnd or not user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid)):return '无法识别'
    handle=kernel.OpenProcess(0x1000,False,pid.value)
    if not handle:return '无法识别'
    try:
        buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):return '无法识别'
        name=os.path.basename(buffer.value).lower()
    finally:kernel.CloseHandle(handle)
    if name in ('chrome.exe','msedge.exe','firefox.exe','brave.exe','qq.exe','wechat.exe'):return '浏览与交流'
    if name in ('code.exe','devenv.exe','excel.exe','winword.exe','powerpnt.exe'):return '工作'
    if name in ('photoshop.exe','blender.exe','obs64.exe','premiere pro.exe'):return '创作'
    return '其他'


class SystemActivity:
    def __init__(self,workbench,idle=None,foreground=None):
        self.w=workbench;self.idle=idle or (lambda:windows_idle_seconds()/1000);self.foreground=foreground or windows_foreground_category
        self.lock=threading.RLock();self.stop_event=threading.Event();self.thread=None;self.running=False
        self.options={};self.away=False;self.away_observations=0;self.last_tick=None;self.previous_idle=None;self.inflight=False;self.events=deque(maxlen=10)
        self.w.s.db.execute('CREATE TABLE IF NOT EXISTS computer_activity_daily (day TEXT NOT NULL,category TEXT NOT NULL,seconds REAL NOT NULL,PRIMARY KEY(day,category))')
        self.w.s.db.execute('CREATE TABLE IF NOT EXISTS proactive_attempts (id TEXT PRIMARY KEY,day TEXT NOT NULL,created REAL NOT NULL,state TEXT NOT NULL)')
        self.w.s.db.execute("UPDATE proactive_attempts SET state='uncertain' WHERE state='running'")

    def start(self,activity,reminder,model_reply,away_seconds,cooldown_seconds,daily_limit,activity_confirmation,model_confirmation,*,include_memory=False,include_history=False,context_confirmation='',start_thread=True):
        if any(type(v) is not bool for v in (activity,reminder,model_reply)) or not (activity or reminder):raise Fault('请选择需要开启的功能')
        if model_reply and not reminder:raise Fault('模型主动互动需要先开启返回提醒')
        if activity and activity_confirmation!='确认记录电脑活动分类':raise Fault('需要确认记录电脑活动分类')
        if model_reply and model_confirmation!='确认按预算自动调用模型':raise Fault('需要确认可能产生费用的自动模型调用')
        if type(include_memory) is not bool or type(include_history) is not bool:raise Fault('上下文选项无效')
        if (include_memory or include_history) and not model_reply:raise Fault('只有模型主动互动可以使用已选择的上下文')
        if (include_memory or include_history) and context_confirmation!='确认将所选私人上下文发送给模型':raise Fault('需要确认所选私人上下文会发送给模型')
        if include_memory and self.w.characters.current()['id']!='default':raise Fault('自定义角色暂不使用大肥鱼的私人记忆')
        if type(away_seconds) is not int or not 300<=away_seconds<=86400 or type(cooldown_seconds) is not int or not 900<=cooldown_seconds<=604800 or type(daily_limit) is not int or not 1<=daily_limit<=10:raise Fault('检测时长或频率设置无效')
        with self.lock:
            if self.running:raise Fault('监测已在运行，请先停止再修改设置')
            if self.inflight or (self.thread and self.thread.is_alive()):raise Fault('上次监测或模型请求仍在结束，请稍后再试')
            self.options={'activity':activity,'reminder':reminder,'model_reply':model_reply,'include_memory':include_memory,'include_history':include_history,'away_seconds':away_seconds,'cooldown_seconds':cooldown_seconds,'daily_limit':daily_limit}
            self.stop_event.clear();self.running=True;self.away=False;self.away_observations=0;self.last_tick=None;self.previous_idle=None
            if start_thread:
                self.thread=threading.Thread(target=self._loop,daemon=True,name='bigfish-local-activity');self.thread.start()
        return self.status()

    def stop(self):
        with self.lock:self.running=False;self.stop_event.set()
        return self.status()

    def close(self):
        self.stop()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=35)

    def status(self):
        with self.lock:return {'running':self.running,'inflight':self.inflight,'options':dict(self.options),'events':list(self.events),'scope':'仅当前 Windows 会话；不读取窗口标题、按键内容或屏幕。停止不撤回已开始的模型请求。'}

    def _event(self,text,*,character=None):
        event={'id':uuid.uuid4().hex,'text':text,'created':time.time(),'kind':'greeting' if character else 'notice'}
        if character:event.update(character_id=character['id'],character_name=character['name'])
        with self.lock:self.events.append(event)

    def reply_draft(self,event_id):
        if not isinstance(event_id,str) or len(event_id)!=32:raise Fault('互动编号无效')
        with self.lock:event=next((dict(item) for item in self.events if item['id']==event_id),None)
        if not event or event['kind']!='greeting':raise Fault('互动已过期或不可回复')
        active=self.w.characters.current()
        if active['id']!=event['character_id']:raise Fault('请先切换回发起问候的角色')
        quote=event['text'][:300]
        return {'text':'你刚才说过：「'+quote+'」\n我想说：','character_name':active['name'],'automatically_sent':False,'saved_as_memory':False}

    def _loop(self):
        while not self.stop_event.wait(15):
            try:self.tick()
            except Exception:
                self._event('无法读取系统活动状态，监测已停止；没有自动重试。');self.stop();return

    def tick(self,*,now=None,monotonic=None):
        now=time.time() if now is None else now;monotonic=time.monotonic() if monotonic is None else monotonic
        with self.lock:
            if not self.running:return
            options=dict(self.options);gap=None if self.last_tick is None else max(0,monotonic-self.last_tick);elapsed=0 if gap is None else min(gap,30);self.last_tick=monotonic
        idle=float(self.idle())
        if not 0<=idle<=604800:raise Fault('系统空闲时间异常')
        with self.lock:
            if not self.running:return
            abnormal=self.previous_idle is not None and gap is not None and idle>self.previous_idle+gap+30
            self.previous_idle=idle
            if abnormal:
                self.away=False;self.away_observations=0
                return
        if options['activity'] and elapsed and idle<300:
            with self.lock:
                if not self.running:return
            category=self.foreground()
            if category not in CATEGORIES:category='无法识别'
            day=datetime.fromtimestamp(now,TZ).date().isoformat()
            with self.lock:
                if not self.running:return
            with self.w.s.lock:self.w.s.db.execute('INSERT INTO computer_activity_daily VALUES (?,?,?) ON CONFLICT(day,category) DO UPDATE SET seconds=seconds+excluded.seconds',(day,category,elapsed))
        with self.lock:
            if not self.running:return
            was_away=self.away
            if idle>=options['away_seconds']:
                self.away_observations+=1
                if self.away_observations>=2:self.away=True
                return
            self.away_observations=0
            if not was_away:return
            self.away=False
        if options['reminder']:self._on_return(now,options)

    def _on_return(self,now,options):
        with self.lock:
            if not self.running:return
        day=datetime.fromtimestamp(now,TZ).date().isoformat()
        with self.w.s.lock:
            last=self.w.s.db.execute('SELECT MAX(created) FROM proactive_attempts').fetchone()[0]
            count=self.w.s.db.execute('SELECT COUNT(*) FROM proactive_attempts WHERE day=?',(day,)).fetchone()[0]
            if (last is not None and (now<last or now-last<options['cooldown_seconds'])) or count>=options['daily_limit']:return
            ident=uuid.uuid4().hex
            self.w.s.db.execute("INSERT INTO proactive_attempts VALUES (?,?,?,'running')",(ident,day,now))
        if not options['model_reply']:
            self._event('欢迎回来，要继续聊聊吗？',character=self.w.characters.current())
            with self.w.s.lock:self.w.s.db.execute("UPDATE proactive_attempts SET state='local' WHERE id=?",(ident,))
            return
        try:
            with self.w.s.lock:c=self.w.s.config()
            if not c['paid_enabled'] or not c['daily_budget_micro'] or not c['request_cap_micro']:raise Fault('预算未启用')
            from .deepseek_test import require_window,OffPeakDeferred
            try:require_window(c['provider_url'])
            except OffPeakDeferred:
                with self.w.s.lock:self.w.s.db.execute("UPDATE proactive_attempts SET state='deferred_not_sent' WHERE id=?",(ident,))
                self._event('当前不在允许的调用时段，本次没有调用模型。');return
            if self.stop_event.is_set():
                with self.w.s.lock:self.w.s.db.execute("UPDATE proactive_attempts SET state='cancelled_not_sent' WHERE id=?",(ident,))
                return
            active=self.w.characters.current()
            if active['id']!='default' and options.get('include_memory'):raise Fault('自定义角色暂不使用大肥鱼的私人记忆')
            prefix='local-owner:' if active['id']=='default' else 'local-role:'+active['id']+':'
            context=self.w.growth.context(Scope.owner(),include_memory=options.get('include_memory',False),max_turns=8 if options.get('include_history') else 0,max_bytes=3500,source_prefix=prefix)
            conversation={'audience':'owner_private','channel':'proactive'}
            if active['id']!='default':
                context['personality']={'version':1,'data':{'name':active['name'],'identity':active['identity'],'traits':active['traits']}}
                conversation['character_kind']='custom'
            with self.lock:
                cancelled=not self.running or self.stop_event.is_set()
                if not cancelled:self.inflight=True
            if cancelled:
                with self.w.s.lock:self.w.s.db.execute("UPDATE proactive_attempts SET state='cancelled_not_sent' WHERE id=?",(ident,))
                return
            result=self.w.model.chat('主人在离开电脑一段时间后回来了。请依据当前角色设定自然地简短问候，不要猜测离开期间发生的事。','proactive-'+ident,context['personality'],context['memories'],conversation=conversation,history=context['messages'],private_uncached=True)
            from .social import validate_reply
            reply=validate_reply(result['text'],public=False)
            self._event(reply,character=active)
            with self.w.s.lock:self.w.s.db.execute("UPDATE proactive_attempts SET state='completed' WHERE id=?",(ident,))
        except Exception:
            with self.w.s.lock:
                charged=self.w.s.db.execute('SELECT 1 FROM charges WHERE id=?',('model:proactive-'+ident,)).fetchone()
                self.w.s.db.execute('UPDATE proactive_attempts SET state=? WHERE id=?',('uncertain' if charged else 'blocked_not_sent',ident))
            with self.lock:self.options['model_reply']=False
            self._event('本次模型请求未能确认或费用状态不明，已暂停自动模型互动；请先核对账单。' if charged else '模型条件未满足，本次没有调用模型；自动模型互动已暂停。')
        finally:
            with self.lock:self.inflight=False

    def report(self,period,now=None):
        if period not in ('day','week'):raise Fault('报告周期无效')
        now=time.time() if now is None else now;end=datetime.fromtimestamp(now,TZ).date();start=end if period=='day' else end-timedelta(days=end.weekday())
        with self.w.s.lock:rows=self.w.s.db.execute('SELECT category,SUM(seconds) AS seconds FROM computer_activity_daily WHERE day>=? AND day<=? GROUP BY category ORDER BY seconds DESC',(start.isoformat(),end.isoformat())).fetchall()
        return {'period':period,'start':start.isoformat(),'through':end.isoformat(),'categories':[{'category':r['category'],'seconds':round(r['seconds'])} for r in rows],'scope':'仅统计开启后的当前 Windows 会话前台程序粗分类；不存程序路径、窗口标题、按键或屏幕。采样值是估计，不代表实际工作时长。'}

    def clear_report(self,confirmation):
        if confirmation!='确认删除电脑活动统计':raise Fault('需要确认删除电脑活动统计')
        with self.w.s.lock:self.w.s.db.execute('DELETE FROM computer_activity_daily')
        return {'deleted':True,'note':'当前统计已删除；磁盘和备份中的残留无法保证立即清除。'}
