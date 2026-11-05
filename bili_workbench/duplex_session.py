"""Streaming duplex session engine with explicitly injected, inert-by-default adapters.

No network, microphone, storage or credential implementation lives here. Adapters
must independently honor authorization, budget, transport and privacy policies.
Audio is bounded 16 kHz mono PCM16; echo suppression is an external capability.
"""
import asyncio
from contextlib import aclosing
from dataclasses import dataclass

from .runtime import Fault

_SHUTDOWN_TIMEOUT=0.5

@dataclass(frozen=True)
class SpeechStarted:
    pass

@dataclass(frozen=True)
class FinalTranscript:
    text: str

class DuplexSession:
    """One explicitly started in-memory session; provider adapters are required.

    recognizer.events(frames) yields SpeechStarted or FinalTranscript.
    model.stream_reply(text) returns a closable async generator of text deltas.
    synthesizer.stream_pcm(text_deltas) is a closable PCM16 async generator.
    speaker.write(frame), speaker.interrupt(), speaker.close() are async.
    authorize_turn() must explicitly return True; it defaults to deny.
    """
    def __init__(self,recognizer,model,synthesizer,speaker,*,authorize_turn=None,queue_frames=16,echo_mode=None):
        if type(queue_frames) is not int or not 1<=queue_frames<=64:raise Fault('音频队列限制无效')
        if echo_mode not in ('headphones','browser-echo-cancellation'):raise Fault('需要明确选择耳机或浏览器回声消除')
        self.recognizer=recognizer;self.model=model;self.synthesizer=synthesizer;self.speaker=speaker
        self.authorize_turn=authorize_turn or (lambda:False)
        self.queue=asyncio.Queue(maxsize=queue_frames);self.state='idle';self.echo_mode=echo_mode
        self._recognition=None;self._response=None;self._stopping=False;self._generation=0;self._failure=None
        self._speaker_tasks=set();self._speaker_closed=False;self._audio_stop_uncertain=False

    async def start(self,confirmation):
        if confirmation!='确认开始语音会话':raise Fault('需要明确确认开始语音会话')
        if self.state!='idle':raise Fault('语音会话已经开始或已结束')
        self.state='listening';self._recognition=asyncio.create_task(self._recognize())
        return {'state':self.state,'echo_mode':self.echo_mode,'echo_effectiveness_verified':False,'microphone_started':False,'network_started':False}

    async def push_audio(self,frame):
        if self.state not in ('listening','responding'):raise Fault('语音会话未运行')
        if type(frame) is not bytes or not 0<len(frame)<=3200 or len(frame)%2:raise Fault('输入须为不超过 100 毫秒的 16 kHz 单声道 PCM16')
        try:self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            result=await self.stop()
            if result['state']!='stopped':raise Fault('音频处理跟不上，组件停止状态不明；不会继续接收录音') from None
            raise Fault('音频处理跟不上，已停止会话；不会积压录音') from None

    async def _frames(self):
        while True:
            yield await self.queue.get()

    async def _speaker_call(self,name):
        try:task=asyncio.create_task(getattr(self.speaker,name)())
        except Exception:
            self._audio_stop_uncertain=True;return False
        self._speaker_tasks.add(task)
        def finished(item):
            self._speaker_tasks.discard(item)
            if not item.cancelled():item.exception()
        task.add_done_callback(finished)
        done,pending=await asyncio.wait({task},timeout=_SHUTDOWN_TIMEOUT)
        if pending:
            task.cancel();self._audio_stop_uncertain=True;return False
        try:task.result();return True
        except (Exception,asyncio.CancelledError):
            self._audio_stop_uncertain=True;return False

    async def _close_speaker(self):
        if self._speaker_closed:return not self._audio_stop_uncertain
        self._speaker_closed=True
        return await self._speaker_call('close')

    async def _interrupt(self):
        self._generation+=1
        response=self._response
        if response and not response.done():response.cancel()
        if not await self._speaker_call('interrupt'):
            self._failure='语音输出设备停止状态不明';self.state='error';raise Fault(self._failure)
        if response and not response.done():
            _,pending=await asyncio.wait({response},timeout=_SHUTDOWN_TIMEOUT)
            if pending:
                self._failure='语音输出未及时停止，会话已中断';self.state='error'
                raise Fault(self._failure)
        self._response=None
        if not self._stopping:self.state='listening'

    async def _recognize(self):
        try:
            async with aclosing(self._frames()) as frames:
                async with aclosing(self.recognizer.events(frames)) as events:
                    async for event in events:
                        if self._stopping:return
                        if type(event) is SpeechStarted:
                            await self._interrupt()
                        elif type(event) is FinalTranscript:
                            text=event.text
                            if not isinstance(text,str) or not text.strip() or len(text)>1000 or len(text.encode('utf8'))>4000 or any(ord(c)<32 and c not in '\n\t' for c in text):raise Fault('语音转写内容无效')
                            if self._response:await self._interrupt()
                            if self.authorize_turn() is not True:
                                self.state='listening';continue
                            generation=self._generation;self.state='responding'
                            self._response=asyncio.create_task(self._respond(text,generation))
                        else:raise Fault('语音识别事件无效')
            if not self._stopping:raise Fault('语音识别流意外结束')
        except asyncio.CancelledError:raise
        except Exception:
            self._failure=self._failure or '语音识别或会话处理失败';self.state='error';self._generation+=1
            if self._response and not self._response.done():self._response.cancel()
            if not self._audio_stop_uncertain:await self._speaker_call('interrupt')
            await self._close_speaker()
            self._clear_frames()

    async def _respond(self,text,generation):
        try:
            # Keep both generators owned until their asynchronous finalizers
            # finish, including when cancellation happens in speaker.write.
            # Otherwise an audio subprocess could outlive a "stopped" reply.
            async with aclosing(self.model.stream_reply(text)) as texts:
                async with aclosing(self.synthesizer.stream_pcm(texts)) as audio:
                    async for frame in audio:
                        if self._stopping or generation!=self._generation:return
                        if type(frame) is not bytes or not 0<len(frame)<=32000 or len(frame)%2:raise Fault('语音输出帧无效')
                        await self.speaker.write(frame)
            if not self._stopping and generation==self._generation:self.state='listening'
        except asyncio.CancelledError:raise
        except Exception:
            if not self._stopping:
                self._failure='模型或语音输出失败，未自动重试';self.state='error';self._generation+=1
                self._clear_frames()
                if not self._audio_stop_uncertain:await self._speaker_call('interrupt')
                await self._close_speaker()
                if self._recognition and not self._recognition.done():self._recognition.cancel()

    def _clear_frames(self):
        while True:
            try:self.queue.get_nowait()
            except asyncio.QueueEmpty:return

    async def stop(self):
        if self.state=='stopped':return {'state':'stopped','automatic_retry':False}
        self._stopping=True;self._generation+=1
        tasks=[x for x in (self._recognition,self._response) if x is not None and x is not asyncio.current_task()]
        for task in tasks:
            if not task.done():task.cancel()
        self._clear_frames()
        if not self._audio_stop_uncertain and not self._speaker_closed:await self._speaker_call('interrupt')
        await self._close_speaker()
        pending=set()
        if tasks:_,pending=await asyncio.wait(tasks,timeout=_SHUTDOWN_TIMEOUT)
        if pending or self._speaker_tasks or self._audio_stop_uncertain:
            self._failure='部分语音组件尚未确认停止';self.state='stopping'
        else:self.state='stopped'
        return {'state':self.state,'automatic_retry':False}

    def status(self):
        return {'state':self.state,'failure':self._failure,'echo_mode':self.echo_mode,'echo_effectiveness_verified':False,'queued_frames':self.queue.qsize()}
