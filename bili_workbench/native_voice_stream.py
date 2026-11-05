"""Opt-in raw PCM process adapter. The Windows host has not been live-tested.

No microphone, playback device, model, installation or policy change is used.
Importing does nothing. Running requires normal readiness and explicit consent.
The caller supplies a complete approved reply, not unchecked model deltas.
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from .runtime import Fault


async def _stop_owned(process):
    if process.returncode is None:
        try:process.kill()
        except OSError:pass
    task=asyncio.create_task(process.wait())
    while True:
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():return task.result()
            # Repeated cancellation cannot pretend the child has exited.
            continue


async def _launch_owned(factory,*args,**kwargs):
    launch=asyncio.create_task(factory(*args,**kwargs))
    try:return await asyncio.shield(launch)
    except asyncio.CancelledError:
        while not launch.done():
            try:await asyncio.shield(launch)
            except asyncio.CancelledError:continue
        process=launch.result()
        await _stop_owned(process)
        raise


async def stream_pcm(text,confirmation,*,process_factory=None,platform=os.name,system_root=None):
    if confirmation is not True:raise Fault('请明确确认本机PCM语音合成')
    if not isinstance(text,str) or not text.strip() or len(text)>1000 or len(text.encode('utf8'))>4000 or any(ord(ch)<32 and ch not in '\n\t' for ch in text):raise Fault('PCM语音文本无效或过长')
    from .social import validate_reply
    validate_reply(text,public=False)
    if platform!='nt':raise Fault('本机PCM合成目前仅支持Windows')
    from .voice_input import readiness
    status=await asyncio.to_thread(readiness)
    if not status.get('ready'):raise Fault('本机语音运行条件未满足；未启动PCM合成')
    executable=Path(system_root or os.environ.get('SystemRoot',''))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    if not executable.is_file():raise Fault('Windows PowerShell不可用')
    script=Path(__file__).with_name('windows_speech_stream.ps1')
    if not script.is_file():raise Fault('PCM语音脚本缺失')
    factory=process_factory or asyncio.create_subprocess_exec
    process=None
    try:
        process=await _launch_owned(factory,str(executable),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=6400,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process.stdin.write(json.dumps({'text':text},ensure_ascii=False).encode('utf8'))
        await process.stdin.drain();process.stdin.close()
        pending=bytearray();total=0;initial=True;deadline=time.monotonic()+120
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise Fault('PCM合成超过总时限')
            block=await asyncio.wait_for(process.stdout.read(3200),timeout=min(15,remaining))
            if not block:break
            if type(block) is not bytes or len(block)>3200:raise Fault('PCM合成输出格式无效')
            total+=len(block)
            if total>16000*2*120:raise Fault('PCM合成输出过长')
            pending.extend(block)
            if initial and pending.startswith((b'RIFF',b'OggS',b'fLaC',b'ID3')):raise Fault('PCM合成意外返回容器格式')
            if len(pending)>=3200:
                initial=False;yield bytes(pending[:3200]);del pending[:3200]
        code=await asyncio.wait_for(process.wait(),timeout=5)
        if code!=0 or total==0 or len(pending)%2:raise Fault('PCM合成未完整成功')
        if pending:yield bytes(pending)
    except (OSError,asyncio.TimeoutError):raise Fault('PCM合成未完成；没有切换其他服务或重试') from None
    finally:
        if process is not None:await _stop_owned(process)
