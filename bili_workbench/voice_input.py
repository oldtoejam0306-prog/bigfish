"""Explicit local WAV transcription through the installed Windows recognizer."""
import base64
import binascii
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import wave
from .runtime import Fault


def readiness():
    """Read policy only; never alter it or attempt to run the blocked script."""
    if os.name!='nt':return {'ready':False,'reason':'本机语音输入目前仅支持 Windows。'}
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    try:
        result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command','Get-ExecutionPolicy'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        policy=result.stdout.decode('ascii','ignore').strip()
    except (OSError,subprocess.TimeoutExpired):return {'ready':False,'reason':'无法检查本机语音运行条件。'}
    if result.returncode or policy not in ('Restricted','AllSigned','RemoteSigned','Unrestricted','Bypass'):
        return {'ready':False,'reason':'无法确认本机语音脚本的运行条件。'}
    if policy in ('Restricted','AllSigned'):
        return {'ready':False,'reason':'当前系统策略不允许运行此语音脚本；没有修改安全设置。'}
    return {'ready':True,'reason':'已检查执行策略；中文识别器及语音识别效果仍需实际验证。'}


def transcribe_wav(encoded):
    if not isinstance(encoded,str) or len(encoded)>550000:raise Fault('录音过长或格式无效')
    try:raw=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError):raise Fault('录音编码无效')
    if len(raw)>400000:raise Fault('录音不能超过 10 秒')
    try:
        with wave.open(io.BytesIO(raw),'rb') as audio:
            if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=16000 or audio.getcomptype()!='NONE' or not 0<audio.getnframes()<=160000:raise Fault('录音必须是 16 kHz 单声道 16 位 WAV，最长 10 秒')
            if len(audio.readframes(audio.getnframes()))!=audio.getnframes()*2:raise Fault('录音内容不完整')
    except (wave.Error,EOFError,struct.error):raise Fault('WAV 录音格式无效')
    if os.name!='nt':raise Fault('本地语音识别当前仅支持 Windows 系统识别器')
    status=readiness()
    if not status.get('ready'):raise Fault(status.get('reason') or '无法确认本机语音运行条件')
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    script=Path(__file__).with_name('windows_speech_input.ps1')
    try:
        result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-File',str(script)],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode!=0 or len(result.stdout)>12000:raise Fault('Windows 语音脚本未运行；请检查系统执行策略和本地识别器')
        data=json.loads(result.stdout.decode('utf-8-sig'))
    except Fault:raise
    except (OSError,subprocess.TimeoutExpired,UnicodeError,ValueError):raise Fault('本地中文识别未完成；没有发送录音')
    text=data.get('text') if isinstance(data,dict) else None
    if not isinstance(text,str) or len(text)>1000:raise Fault('本地识别结果无效')
    return {'text':text,'recognized':bool(text.strip()),'source':'Windows 本地语音识别','model_called':False,'audio_persisted':False}
