"""Explicit continuous PCM host adapter; Windows engine remains untested.

No device is opened. PCM is supplied by the caller after its own authorization.
Only complete, ordered recognition results can become model input. No retry,
fallback, transcript file, credential or system-policy change is implemented.
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from contextlib import aclosing
from .runtime import Fault
from .duplex_session import SpeechStarted,FinalTranscript
from .native_voice_stream import _launch_owned,_stop_owned


class RecognitionProtocol:
    def __init__(self):self.seq=0;self.ready=False;self.completed=False;self.utterance=0;self.active=None;self.finals=0;self.rejected=0
    def accept(self,raw):
        if not isinstance(raw,bytes) or not 0<len(raw)<=8192 or not raw.endswith(b'\n') or self.completed:raise Fault('持续识别事件不完整或已结束')
        try:item=json.loads(raw.decode('utf8'))
        except (ValueError,UnicodeError):raise Fault('持续识别事件编码无效') from None
        if not isinstance(item,dict) or type(item.get('seq')) is not int or item['seq']!=self.seq+1:raise Fault('持续识别事件序号冲突，不重放')
        self.seq=item['seq']
        if self.seq>4096:raise Fault('持续识别事件超过本次限制')
        kind=item.get('type')
        if kind=='error':raise Fault('持续识别宿主报告失败，未自动重启')
        if not self.ready:
            expected={'seq':1,'type':'ready','version':1,'rate':16000,'channels':1,'sample_bytes':2}
            if item!=expected or any(type(item.get(key)) is not int for key in ('version','rate','channels','sample_bytes')):raise Fault('持续识别握手或PCM格式无效')
            self.ready=True;return None
        if kind=='speech':
            if set(item)!={'seq','type','utterance'} or self.active is not None or type(item['utterance']) is not int or item['utterance']!=self.utterance+1:raise Fault('持续识别语句边界冲突')
            self.utterance=item['utterance'];self.active=self.utterance;return SpeechStarted()
        if kind in ('final','rejected'):
            fields={'seq','type','utterance','text'} if kind=='final' else {'seq','type','utterance'}
            if set(item)!=fields or self.active is None or type(item['utterance']) is not int or item['utterance']!=self.active:raise Fault('持续识别结果不属于当前语句')
            self.active=None
            if kind=='rejected':self.rejected+=1;return None
            text=item['text']
            if not isinstance(text,str) or not text.strip() or len(text)>1000 or len(text.encode('utf8'))>4000 or any(ord(ch)<32 and ch not in '\n\t' for ch in text):raise Fault('持续识别最终文本无效')
            self.finals+=1;return FinalTranscript(text)
        if kind=='completed':
            if set(item)!={'seq','type','ok'} or item['ok'] is not True or self.active is not None:raise Fault('持续识别未确认正常结束')
            self.completed=True;return None
        raise Fault('持续识别事件类型无效；不将部分文本交给模型')


async def _finish_tasks(tasks):
    pending=asyncio.gather(*tasks,return_exceptions=True)
    while True:
        try:return await asyncio.shield(pending)
        except asyncio.CancelledError:
            if pending.done():return pending.result()


class NativeContinuousRecognizer:
    def __init__(self,*,confirmation=False,process_factory=None,platform=os.name,system_root=None):
        self.confirmation=confirmation;self.factory=process_factory;self.platform=platform;self.root=system_root;self.protocol=None;self.started=False
    async def events(self,frames):
        async with aclosing(frames):
            async with aclosing(self._events(frames)) as events:
                async for event in events:yield event
    async def _events(self,frames):
        if self.started:raise Fault('持续识别对象已使用，不自动重启')
        self.started=True
        if self.confirmation is not True:raise Fault('请明确确认本机持续识别')
        if self.platform!='nt':raise Fault('持续识别宿主目前仅支持Windows')
        from .voice_input import readiness
        status=await asyncio.to_thread(readiness)
        if not status.get('ready'):raise Fault('本机语音运行条件未满足；未启动持续识别')
        executable=Path(self.root or os.environ.get('SystemRoot',''))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
        script=Path(__file__).with_name('windows_speech_continuous.ps1')
        if not executable.is_file() or not script.is_file() or not script.with_name('native_speech_host.cs').is_file():raise Fault('持续识别宿主文件不可用')
        protocol=RecognitionProtocol();self.protocol=protocol;process=None;writer=None;reader=None;state={'eof':False};deadline=time.monotonic()+600
        try:
            process=await _launch_owned(self.factory or asyncio.create_subprocess_exec,str(executable),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=8192,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            raw=await asyncio.wait_for(process.stdout.readline(),timeout=15)
            protocol.accept(raw)
            if not protocol.ready:raise Fault('持续识别宿主未就绪')
            async def pump():
                total=0
                try:
                    async with aclosing(frames):
                        async for frame in frames:
                            if type(frame) is not bytes or not 0<len(frame)<=3200 or len(frame)%2:raise Fault('持续识别PCM输入格式无效')
                            total+=len(frame)
                            if total>16000*2*600:raise Fault('持续识别输入超过本次时长限制')
                            process.stdin.write(frame);await asyncio.wait_for(process.stdin.drain(),timeout=3)
                    state['eof']=True
                finally:process.stdin.close()
            writer=asyncio.create_task(pump())
            while not protocol.completed:
                if time.monotonic()>deadline:raise Fault('持续识别会话超过时限')
                if reader is None:reader=asyncio.create_task(process.stdout.readline())
                watched={reader} if writer.done() else {reader,writer}
                done,_=await asyncio.wait(watched,timeout=1,return_when=asyncio.FIRST_COMPLETED)
                if writer.done():writer.result()
                if reader not in done:continue
                raw=reader.result();reader=None
                if not raw:raise Fault('持续识别宿主提前退出或缺少完成回执')
                event=protocol.accept(raw)
                if event is not None:yield event
            if not state['eof']:raise Fault('持续识别在输入结束前意外完成')
            await writer
            if await asyncio.wait_for(process.stdout.read(1),timeout=5):raise Fault('持续识别完成后出现额外输出')
            if await asyncio.wait_for(process.wait(),timeout=5)!=0:raise Fault('持续识别宿主退出失败')
        except (OSError,ValueError,asyncio.TimeoutError):raise Fault('持续识别管道未完成；没有切换服务或重试') from None
        finally:
            tasks=[task for task in (writer,reader) if task is not None]
            for task in tasks:
                if not task.done():task.cancel()
            if process is not None:await _stop_owned(process)
            if tasks:await _finish_tasks(tasks)
