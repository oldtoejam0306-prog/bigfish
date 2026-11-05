"""Opt-in duplex adapters; no device, network or credential access at import.

The existing local chat adapter emits a whole response, not streaming tokens.
Buffered recognition uses a simple energy gate, not a production-quality VAD.
The workbench supplies authenticated loopback transport and browser audio;
real devices and speech components remain unverified. Nothing starts at import.
"""
import asyncio
import base64
import io
import wave
import struct
import threading
import uuid
from .duplex_session import SpeechStarted,FinalTranscript
from .runtime import Fault

async def _await_worker(worker,cancelled=None):
    """Do not report cancellation complete while a synchronous worker is running."""
    try:return await asyncio.shield(worker)
    except asyncio.CancelledError:
        if cancelled is not None:cancelled.set()
        while not worker.done():
            try:await asyncio.shield(worker)
            except asyncio.CancelledError:continue
            except Exception:break
        if worker.done() and not worker.cancelled():
            try:worker.result()
            except Exception:pass
        raise

class LocalChatModelAdapter:
    """Reuse LocalChat's explicit memory selection and model budget/ledger gates.

    Cancellation stops delivery, not a running HTTP request. The async response
    remains pending until the worker finishes so the session cannot report that
    the operation was safely cancelled while a request may still be billed.
    """
    def __init__(self,local_chat,*,include_memory=False,include_knowledge=False,stream_model=False):
        if type(include_memory) is not bool or type(include_knowledge) is not bool:raise Fault('上下文授权选项无效')
        if type(stream_model) is not bool:raise Fault('模型流选项无效')
        self.stream_model=stream_model
        self.local_chat=local_chat;self.include_memory=include_memory;self.include_knowledge=include_knowledge

    async def stream_reply(self,text):
        cancelled=threading.Event();request_id='voice-'+uuid.uuid4().hex
        from .model_stream import StreamControl
        control=StreamControl() if self.stream_model else None
        def call():
            if cancelled.is_set():return None
            extra={'stream_control':control} if control is not None else {}
            return self.local_chat.send(request_id,text,self.include_memory,self.include_knowledge,cancel_check=cancelled.is_set,**extra)
        class Cancel:
            def set(self):
                cancelled.set()
                if control is not None:control.cancel()
        worker=asyncio.create_task(asyncio.to_thread(call))
        result=await _await_worker(worker,Cancel())
        if not isinstance(result,dict) or result.get('status')!='completed':raise Fault('对话没有确认完成，不自动重试')
        reply=result.get('reply')
        if not isinstance(reply,str) or not reply.strip() or len(reply)>1000:raise Fault('对话回复无效')
        yield reply

class WindowsBufferedTranscriber:
    """Use the existing guarded Windows recognizer for one PCM16 segment."""
    def __init__(self,transcribe_wav=None):self.transcribe_wav=transcribe_wav
    async def __call__(self,pcm):
        if type(pcm) is not bytes or not 0<len(pcm)<=320000 or len(pcm)%2:raise Fault('语音片段格式无效')
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        encoded=base64.b64encode(buffer.getvalue()).decode('ascii')
        if self.transcribe_wav is None:
            from .voice_input import transcribe_wav
            recognizer=transcribe_wav
        else:recognizer=self.transcribe_wav
        worker=asyncio.create_task(asyncio.to_thread(recognizer,encoded))
        result=await _await_worker(worker)
        if not isinstance(result,dict) or type(result.get('recognized')) is not bool or not isinstance(result.get('text'),str) or len(result['text'])>1000:raise Fault('语音转写结果无效')
        return result['text'] if result['recognized'] else ''


def _wav_to_pcm16_16k(data):
    if type(data) is not bytes or len(data)>12_000_000:raise Fault('语音音频无效')
    try:
        with wave.open(io.BytesIO(data),'rb') as wav:
            channels,width,rate,count=wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()
            if channels not in (1,2) or width!=2 or not 8000<=rate<=96000 or not 0<count<=rate*120:raise Fault('仅支持单声道或立体声 PCM16 系统语音')
            raw=wav.readframes(count)
        if len(raw)!=count*channels*2:raise Fault('语音音频不完整')
    except (wave.Error,EOFError,OSError):raise Fault('语音音频格式无效') from None
    from array import array
    samples=array('h');samples.frombytes(raw)
    import sys
    if sys.byteorder!='little':samples.byteswap()
    if channels==2:samples=array('h',((samples[i]+samples[i+1])//2 for i in range(0,len(samples),2)))
    output=array('h');target=count*16000//rate
    if target<=0:raise Fault('语音音频过短')
    for i in range(target):
        position=i*rate;index=position//16000;fraction=position%16000
        nxt=min(index+1,count-1)
        output.append((samples[index]*(16000-fraction)+samples[nxt]*fraction)//16000)
    if sys.byteorder!='little':output.byteswap()
    return output.tobytes()


async def _validated_speech_text(texts,*,max_chars,max_bytes):
    """Commit a complete bounded reply before irreversible speech output.

    This is a second whole-message rule check, not a credential detector. The
    Model/LocalChat producer must still perform its full private-context checks.
    A partial stream, failed producer or cancelled collection emits no speech.
    """
    from .social import validate_reply
    parts=[];chars=0;size=0;pieces=0
    async for piece in texts:
        if not isinstance(piece,str) or any(ord(ch)<32 and ch not in '\n\t' for ch in piece):raise Fault('语音文本格式无效')
        pieces+=1;chars+=len(piece);size+=len(piece.encode('utf8'))
        if chars>max_chars or size>max_bytes or pieces>4096:raise Fault('语音回复超过限制')
        parts.append(piece)
    value=''.join(parts)
    return validate_reply(value,public=False)

class WindowsBufferedSynthesizer:
    """Adapt existing guarded SAPI WAV output to PCM frames after full reply."""
    def __init__(self,synthesize=None):self.synthesize=synthesize
    async def stream_pcm(self,texts):
        joined=await _validated_speech_text(texts,max_chars=300,max_bytes=1200)
        if self.synthesize is None:
            from .system_voice import synthesize
            synthesizer=synthesize
        else:synthesizer=self.synthesize
        cancelled=threading.Event()
        def render():
            extra={'cancel_check':cancelled.is_set} if self.synthesize is None else {}
            return synthesizer(joined,True,**extra)
        worker=asyncio.create_task(asyncio.to_thread(render))
        data=await _await_worker(worker,cancelled)
        convert=asyncio.create_task(asyncio.to_thread(_wav_to_pcm16_16k,data))
        pcm=await _await_worker(convert)
        for index in range(0,len(pcm),3200):yield pcm[index:index+3200]

class WindowsSegmentedSynthesizer:
    """Validate the complete reply, then speak bounded buffered sentences.

    Existing LocalChatModelAdapter performs private-context checks first. This
    additional gate refuses to speak even a safe prefix before the producer has
    ended successfully; a sentence boundary is not a validation boundary.
    """
    def __init__(self,synthesize=None):self.buffered=WindowsBufferedSynthesizer(synthesize)
    async def stream_pcm(self,texts):
        pending=await _validated_speech_text(texts,max_chars=1000,max_bytes=4000);spoken=False
        async def one(value):yield value
        while pending:
            cut=next((i+1 for i,ch in enumerate(pending[:200]) if ch in '。！？!?\n'),None)
            if cut is None:
                if len(pending)<200:break
                cut=200
            segment,pending=pending[:cut],pending[cut:]
            if not segment.strip():continue
            spoken=True
            async for frame in self.buffered.stream_pcm(one(segment)):yield frame
        if pending.strip():
            spoken=True
            async for frame in self.buffered.stream_pcm(one(pending)):yield frame
        if not spoken:raise Fault('语音回复为空')


class WindowsPCMStreamSynthesizer:
    """Optional native PCM pipe after complete reply validation.

    The process reader/cancellation are fixture-tested. Actual Windows synthesis
    latency, voice availability and streaming behavior still require acceptance.
    """
    def __init__(self,streamer=None):self.streamer=streamer
    async def stream_pcm(self,texts):
        text=await _validated_speech_text(texts,max_chars=1000,max_bytes=4000)
        if self.streamer is None:
            from .native_voice_stream import stream_pcm
            render=stream_pcm
        else:render=self.streamer
        source=render(text,True)
        try:
            async for frame in source:yield frame
        finally:await source.aclose()

class EnergySegmentRecognizer:
    """Bounded PCM16 energy segmentation with an injected async transcriber.

    It emits speech start early and final text only after silence or ten seconds.
    This simple method is not reliable VAD and is not real-time transcription;
    no audio is saved, and an actual transcriber must be explicitly supplied.
    """
    def __init__(self,transcribe_pcm,*,threshold=600,onset_ms=40,silence_ms=600,max_seconds=10):
        if not callable(transcribe_pcm):raise Fault('缺少语音转写组件')
        if any(type(x) is not int for x in (threshold,onset_ms,silence_ms,max_seconds)) or not 1<=threshold<=30000 or not 20<=onset_ms<=500 or not 100<=silence_ms<=3000 or not 1<=max_seconds<=10:raise Fault('语音分段参数无效')
        self.transcribe=transcribe_pcm;self.threshold=threshold;self.onset=16*onset_ms;self.silence=16*silence_ms;self.maximum=16000*max_seconds

    async def events(self,frames):
        chunks=[];voiced_samples=0;quiet_samples=0;total_samples=0;active=False
        async for frame in frames:
            if type(frame) is not bytes or not 0<len(frame)<=3200 or len(frame)%2:raise Fault('语音输入帧格式无效')
            count=len(frame)//2
            power=sum(sample[0]*sample[0] for sample in struct.iter_unpack('<h',frame))//count
            voiced=power>=self.threshold*self.threshold
            if not active:
                if not voiced:chunks=[];voiced_samples=0;total_samples=0;continue
                chunks.append(frame);voiced_samples+=count;total_samples+=count
                if voiced_samples<self.onset:continue
                active=True;yield SpeechStarted()
            else:
                chunks.append(frame);total_samples+=count
            quiet_samples=0 if voiced else quiet_samples+count
            if total_samples>self.maximum:raise Fault('语音片段超过长度限制，已停止而非丢弃音频')
            if quiet_samples>=self.silence or total_samples>=self.maximum:
                pcm=b''.join(chunks);chunks=[];voiced_samples=quiet_samples=total_samples=0;active=False
                result=await self.transcribe(pcm)
                if not isinstance(result,str) or len(result)>1000:raise Fault('语音转写结果无效')
                if result.strip():yield FinalTranscript(result)

class QueueSpeaker:
    """Bounded in-memory PCM bridge; consumer must implement actual playback."""
    def __init__(self,*,frames=16):
        if type(frames) is not int or not 1<=frames<=64:raise Fault('播放队列限制无效')
        self.queue=asyncio.Queue(maxsize=frames);self.closed=False;self.generation=0
    async def write(self,frame):
        if self.closed:raise Fault('播放队列已关闭')
        if type(frame) is not bytes or not 0<len(frame)<=32000 or len(frame)%2:raise Fault('播放帧格式无效')
        generation=self.generation
        await self.queue.put((generation,frame))
        if self.closed or generation!=self.generation:raise Fault('播放已中断')
    async def interrupt(self):
        self.generation+=1
        while True:
            try:self.queue.get_nowait()
            except asyncio.QueueEmpty:break
        if self.closed:self.queue.put_nowait((self.generation,None))
    async def close(self):
        if self.closed:return
        self.closed=True;await self.interrupt()
    async def read(self):
        if self.closed and self.queue.empty():raise Fault('播放队列已关闭')
        while True:
            generation,frame=await self.queue.get()
            if frame is None or self.closed:raise Fault('播放队列已关闭')
            if generation==self.generation:return frame
