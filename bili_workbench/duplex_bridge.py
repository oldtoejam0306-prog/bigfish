"""Bounded, in-memory duplex bridge for an authorized local transport.

This does not expose an HTTP endpoint, start a device or authorize a model.
Call methods on one asyncio event loop; callers must authenticate the client,
obtain microphone consent and enforce network/credential/budget policy.
"""
import asyncio
import hashlib
import math
import re
import secrets
from .runtime import Fault

class DuplexBridge:
    def __init__(self,session,speaker,*,inactivity_seconds=10):
        if type(inactivity_seconds) not in (int,float) or not math.isfinite(inactivity_seconds) or not .01<=inactivity_seconds<=120:raise Fault('语音会话空闲限制无效')
        self.session=session;self.speaker=speaker;self.id=None;self._input=0;self._last_hash=None
        self._out=0;self._acked=0;self._pending=None;self._stopping=False;self._lock=asyncio.Lock()
        self._inactivity=float(inactivity_seconds);self._last_activity=None;self._watchdog=None

    async def start(self,confirmation,*,headphones_confirmed=False,browser_echo_reported=False):
        async with self._lock:
            if self.id is not None:raise Fault('语音桥接已开始，不能重复启动')
            if type(headphones_confirmed) is not bool or type(browser_echo_reported) is not bool:raise Fault('回声控制确认无效')
            mode=self.session.echo_mode
            if mode=='headphones' and not headphones_confirmed:raise Fault('请先确认使用耳机，避免播放声音进入麦克风')
            if mode=='browser-echo-cancellation' and not browser_echo_reported:raise Fault('浏览器未报告启用回声消除，请使用耳机或检查音频设置')
            if mode not in ('headphones','browser-echo-cancellation'):raise Fault('回声控制模式无效')
            state=await self.session.start(confirmation)
            self.id=secrets.token_hex(16);self._last_activity=asyncio.get_running_loop().time()
            self._watchdog=asyncio.create_task(self._watch_idle())
            return {'id':self.id,'state':state['state'],'echo_mode':mode,'echo_effectiveness_verified':False,'microphone_started':False,'network_started':False}

    async def _watch_idle(self):
        while True:
            await asyncio.sleep(min(1.0,self._inactivity/2))
            async with self._lock:
                if self._stopping:return
                if asyncio.get_running_loop().time()-self._last_activity>=self._inactivity:
                    self._stopping=True;self._pending=None
                    try:await self.session.stop()
                    except Exception:pass
                    return

    def _check(self,ident):
        if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident) or self.id is None or not secrets.compare_digest(ident,self.id):raise Fault('语音会话无效')

    async def feed(self,ident,sequence,frame):
        async with self._lock:
            self._check(ident)
            if self._stopping:raise Fault('语音会话正在停止')
            if type(sequence) is not int or sequence<=0:raise Fault('音频序号无效')
            if type(frame) is not bytes or not 0<len(frame)<=3200 or len(frame)%2:raise Fault('音频帧格式无效')
            fingerprint=hashlib.sha256(frame).digest()
            if sequence==self._input:
                if fingerprint!=self._last_hash:raise Fault('相同音频序号内容不一致')
                self._last_activity=asyncio.get_running_loop().time()
                return {'accepted':True,'duplicate':True,'sequence':sequence}
            if sequence!=self._input+1:raise Fault('音频顺序不连续，请停止会话并检查')
            self._last_activity=asyncio.get_running_loop().time()
            await self.session.push_audio(frame)
            self._input=sequence;self._last_hash=fingerprint
            return {'accepted':True,'duplicate':False,'sequence':sequence}

    async def poll(self,ident,ack,*,status_only=False):
        async with self._lock:
            self._check(ident)
            if type(status_only) is not bool:raise Fault('播放状态选项无效')
            if type(ack) is not int or ack<self._acked or ack>self._out:raise Fault('播放确认序号无效')
            if not self._stopping:self._last_activity=asyncio.get_running_loop().time()
            self._acked=ack;generation=self.speaker.generation
            if self._pending and self._pending[0]!=generation:self._pending=None
            if self._pending and ack>=self._pending[1]:self._pending=None
            if not self._pending and not self._stopping and not status_only:
                while True:
                    try:gen,frame=self.speaker.queue.get_nowait()
                    except asyncio.QueueEmpty:break
                    if frame is None:break
                    if gen==generation and not self.speaker.closed:
                        self._out+=1;self._pending=(gen,self._out,frame);break
            output=None if status_only or not self._pending else {'sequence':self._pending[1],'pcm':self._pending[2]}
            return {'state':self.session.status()['state'],'generation':generation,'output':output,'client_must_deduplicate':True}

    async def stop(self,ident):
        async with self._lock:
            self._check(ident);self._stopping=True;self._pending=None
            watchdog=self._watchdog;self._watchdog=None
            if watchdog is not None and watchdog is not asyncio.current_task() and not watchdog.done():
                watchdog.cancel();await asyncio.gather(watchdog,return_exceptions=True)
            return await self.session.stop()
