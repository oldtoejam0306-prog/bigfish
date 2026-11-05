"""Explicit local Windows SAPI preview. No model download or network service."""
import json,os,subprocess,tempfile,time,wave
from pathlib import Path
from .runtime import Fault

def _cancellable_process(args,payload,cancel_check,process_factory):
    if cancel_check():raise Fault('本机语音已取消，没有启动合成')
    process=process_factory(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    first=True;deadline=time.monotonic()+45
    try:
        while True:
            cancelled=cancel_check()
            if cancelled or time.monotonic()>=deadline:
                # Only this exact owned child is stopped. No PID discovery,
                # policy override, alternate host or script retry is involved.
                try:process.kill()
                except OSError:pass
                process.communicate()  # worker stays pending until exit is known
                raise Fault('本机语音已取消' if cancelled else '本机语音合成超时')
            try:
                process.communicate(input=payload if first else None,timeout=.1)
                if cancel_check():raise Fault('本机语音已取消，不返回音频')
                return process.returncode
            except subprocess.TimeoutExpired:first=False
    finally:
        if process.poll() is None:
            try:process.kill()
            except OSError:pass
            process.communicate()


def synthesize(text, confirmation, *, runner=subprocess.run, platform=os.name, system_root=None,cancel_check=None,process_factory=None):
    if confirmation is not True:raise Fault('请明确选择试听本机系统语音')
    if not isinstance(text,str) or not text.strip() or len(text)>300 or len(text.encode('utf-8'))>1200 or any(ord(c)<32 and c not in '\n\t' for c in text):raise Fault('试听文本须为1至300字，且不能包含控制字符')
    if platform!='nt':raise Fault('本机系统语音试听仅支持 Windows')
    if cancel_check is not None and not callable(cancel_check):raise Fault('语音取消检查无效')
    if cancel_check is not None and cancel_check():raise Fault('本机语音已取消，没有启动合成')
    from .voice_input import readiness
    status=readiness()
    if not status.get('ready'):raise Fault(status.get('reason') or '无法确认本机语音运行条件')
    root=Path(system_root or os.environ.get('SystemRoot',''))
    exe=root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    if not exe.is_file():raise Fault('Windows PowerShell 不可用，不能启动系统语音')
    script=Path(__file__).with_name('windows_speech_output.ps1')
    with tempfile.TemporaryDirectory(prefix='bigfish-voice-preview-') as tmp:
        output=Path(tmp)/'preview.wav'
        payload=json.dumps({'text':text,'output':str(output)},ensure_ascii=False)
        try:
            args=[str(exe),'-NoLogo','-NoProfile','-NonInteractive','-File',str(script)]
            if cancel_check is None:
                result=runner(args,input=payload,encoding='utf-8',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));returncode=result.returncode
            else:returncode=_cancellable_process(args,payload,cancel_check,process_factory or subprocess.Popen)
        except (OSError,subprocess.TimeoutExpired):raise Fault('本机语音试听未完成，请检查系统语音后重试') from None
        if returncode!=0 or not output.is_file():raise Fault('本机语音脚本或中文声音不可用；未使用网络语音')
        if output.stat().st_size>12_000_000:raise Fault('试听音频超过大小限制')
        try:
            with wave.open(str(output),'rb') as wav:
                if wav.getnchannels() not in (1,2) or wav.getsampwidth() not in (1,2,3,4) or not 8000<=wav.getframerate()<=96000 or not 0<wav.getnframes()<=wav.getframerate()*120:raise Fault('试听音频格式无效')
            return output.read_bytes()
        except (wave.Error,EOFError,OSError):raise Fault('试听音频格式无效') from None
