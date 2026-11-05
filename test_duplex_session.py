import asyncio,unittest
from unittest.mock import patch
from bili_workbench.duplex_session import DuplexSession,SpeechStarted,FinalTranscript
from bili_workbench.runtime import Fault

class Recognizer:
 async def events(self,frames):
  async for frame in frames:
   if frame==b'\x01\x00':yield SpeechStarted()
   elif frame==b'\x02\x00':yield FinalTranscript('模拟语音')

class Model:
 def __init__(self,block=False,fail=False):self.calls=[];self.block=block;self.fail=fail;self.started=asyncio.Event();self.cancelled=asyncio.Event()
 async def stream_reply(self,text):
  self.calls.append(text);self.started.set()
  if self.fail:raise RuntimeError('fixture failure')
  try:
   yield '你好'
   if self.block:await asyncio.Event().wait()
   yield '呀'
  finally:
   if self.block:self.cancelled.set()

class Synth:
 async def stream_pcm(self,texts):
  async for text in texts:yield b'\x00\x01' * len(text)

class Speaker:
 def __init__(self):self.frames=[];self.interruptions=0;self.closed=0;self.written=asyncio.Event()
 async def write(self,data):self.frames.append(data);self.written.set()
 async def interrupt(self):self.interruptions+=1
 async def close(self):self.closed+=1

class DuplexSessionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.model=Model();self.speaker=Speaker();self.sessions=[]
 async def asyncTearDown(self):
  for s in self.sessions:await s.stop()
 def session(self,**kw):
  s=DuplexSession(Recognizer(),self.model,Synth(),self.speaker,echo_mode='headphones',**kw);self.sessions.append(s);return s
 async def wait_until(self,condition):
  async def spin():
   while not condition():await asyncio.sleep(.001)
  await asyncio.wait_for(spin(),1)
 async def test_explicit_start_default_denies_model(self):
  s=self.session()
  with self.assertRaises(Fault):await s.start('')
  with self.assertRaises(Fault):await s.push_audio(b'\x01\x00')
  status=await s.start('确认开始语音会话');self.assertFalse(status['microphone_started']);self.assertFalse(status['network_started'])
  await s.push_audio(b'\x02\x00');await asyncio.sleep(.03);self.assertEqual(self.model.calls,[]);self.assertEqual(self.speaker.frames,[])
 async def test_mock_streaming_turn_reaches_speaker(self):
  s=self.session(authorize_turn=lambda:True);await s.start('确认开始语音会话');await s.push_audio(b'\x01\x00');await s.push_audio(b'\x02\x00')
  await self.wait_until(lambda:len(self.speaker.frames)==2)
  self.assertEqual(self.model.calls,['模拟语音']);self.assertEqual(s.status()['state'],'listening');self.assertFalse(s.status()['echo_effectiveness_verified'])
 async def test_stop_waits_for_audio_generator_cleanup_before_confirming_exit(self):
  release=asyncio.Event();closing=asyncio.Event();blocked=asyncio.Event()
  class ClosingSynth:
   async def stream_pcm(self,texts):
    try:yield b'\x01\x00'
    finally:closing.set();await release.wait()
  class BlockingSpeaker(Speaker):
   async def write(self,data):blocked.set();await asyncio.Event().wait()
  s=DuplexSession(Recognizer(),self.model,ClosingSynth(),BlockingSpeaker(),authorize_turn=lambda:True,echo_mode='headphones');self.sessions.append(s)
  try:
   await s.start('确认开始语音会话');await s.push_audio(b'\x02\x00');await asyncio.wait_for(blocked.wait(),1)
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',.03):
    result=await s.stop();self.assertEqual(result['state'],'stopping');self.assertTrue(closing.is_set());self.assertFalse(s._response.done())
   release.set()
   try:await asyncio.wait_for(s._response,1)
   except asyncio.CancelledError:pass
   self.assertEqual((await s.stop())['state'],'stopped')
  finally:release.set()
 async def test_stop_waits_for_recognizer_generator_cleanup_during_interrupt(self):
  release=asyncio.Event();closing=asyncio.Event();interrupting=asyncio.Event()
  class ClosingRecognizer:
   async def events(self,frames):
    try:yield SpeechStarted()
    finally:closing.set();await release.wait()
  s=DuplexSession(ClosingRecognizer(),self.model,Synth(),self.speaker,echo_mode='headphones');self.sessions.append(s)
  async def interrupt():interrupting.set();await asyncio.Event().wait()
  s._interrupt=interrupt
  try:
   await s.start('确认开始语音会话');await asyncio.wait_for(interrupting.wait(),1)
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',.03):
    self.assertEqual((await s.stop())['state'],'stopping');self.assertTrue(closing.is_set());self.assertFalse(s._recognition.done())
   release.set()
   try:await asyncio.wait_for(s._recognition,1)
   except asyncio.CancelledError:pass
   self.assertEqual((await s.stop())['state'],'stopped')
  finally:release.set()
 async def test_speech_interrupts_inflight_stream_without_retry(self):
  self.model=Model(block=True);s=self.session(authorize_turn=lambda:True);await s.start('确认开始语音会话');await s.push_audio(b'\x02\x00')
  await asyncio.wait_for(self.speaker.written.wait(),1);before=len(self.speaker.frames);await s.push_audio(b'\x01\x00');await asyncio.wait_for(self.model.cancelled.wait(),1)
  await self.wait_until(lambda:self.speaker.interruptions>=1 and s.status()['state']=='listening');self.assertEqual(len(self.speaker.frames),before);self.assertEqual(len(self.model.calls),1)
 async def test_queue_overflow_stops_instead_of_retaining_audio(self):
  class StalledRecognizer:
   async def events(self,frames):
    await asyncio.Event().wait()
    if False:yield None
  s=DuplexSession(StalledRecognizer(),self.model,Synth(),self.speaker,queue_frames=1,echo_mode='headphones');self.sessions.append(s)
  await s.start('确认开始语音会话');await s.push_audio(b'\x00\x00')
  with self.assertRaises(Fault):await s.push_audio(b'\x00\x00')
  self.assertEqual(s.status()['state'],'stopped');self.assertEqual(s.status()['queued_frames'],0);self.assertEqual(self.model.calls,[])
 async def test_overflow_does_not_claim_stop_if_speaker_stalls(self):
  class StalledRecognizer:
   async def events(self,frames):
    await asyncio.Event().wait()
    if False:yield None
  class StalledSpeaker(Speaker):
   def __init__(self):super().__init__();self.release=asyncio.Event()
   async def interrupt(self):
    while not self.release.is_set():
     try:await self.release.wait()
     except asyncio.CancelledError:continue
  speaker=StalledSpeaker();s=DuplexSession(StalledRecognizer(),self.model,Synth(),speaker,queue_frames=1,echo_mode='headphones');self.sessions.append(s)
  try:
   await s.start('确认开始语音会话');await s.push_audio(b'\x00\x00')
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',0.03):
    with self.assertRaises(Fault) as error:await s.push_audio(b'\x00\x00')
    self.assertIn('停止状态不明',str(error.exception));self.assertEqual(s.status()['state'],'stopping');self.assertEqual(s.status()['queued_frames'],0)
    speaker.release.set();await self.wait_until(lambda:not s._speaker_tasks)
  finally:speaker.release.set()
 async def test_invalid_frames_and_echo_mode_are_rejected(self):
  with self.assertRaises(Fault):DuplexSession(Recognizer(),self.model,Synth(),self.speaker)
  s=self.session();await s.start('确认开始语音会话')
  for frame in (b'',b'x',b'\x00'*3202,'not bytes'):
   with self.assertRaises(Fault):await s.push_audio(frame)
  self.assertEqual(self.model.calls,[])
 async def test_model_failure_stops_without_retry(self):
  self.model=Model(fail=True);s=self.session(authorize_turn=lambda:True);await s.start('确认开始语音会话');await s.push_audio(b'\x02\x00')
  await self.wait_until(lambda:s.status()['state']=='error' and self.speaker.closed>=1);self.assertEqual(len(self.model.calls),1)
  with self.assertRaises(Fault):await s.push_audio(b'\x00\x00')
 async def test_uncooperative_model_reports_stopping_without_retry(self):
  class StubbornModel:
   def __init__(self):self.release=asyncio.Event();self.started=asyncio.Event();self.calls=0
   async def stream_reply(self,text):
    self.calls+=1;yield '起始';self.started.set()
    while not self.release.is_set():
     try:await self.release.wait()
     except asyncio.CancelledError:continue
    yield '迟到的数据'
  model=StubbornModel();self.model=model;s=self.session(authorize_turn=lambda:True);await s.start('确认开始语音会话');await s.push_audio(b'\x02\x00')
  try:
   await asyncio.wait_for(model.started.wait(),1);before=len(self.speaker.frames)
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',0.03):
    result=await s.stop();self.assertEqual(result['state'],'stopping');self.assertEqual(model.calls,1)
    model.release.set();await self.wait_until(lambda:s._response.done());self.assertEqual(len(self.speaker.frames),before)
    self.assertEqual((await s.stop())['state'],'stopped')
  finally:model.release.set()
 async def test_uncooperative_speaker_does_not_claim_stopped(self):
  class StubbornSpeaker(Speaker):
   def __init__(self):super().__init__();self.release=asyncio.Event();self.entered=asyncio.Event()
   async def interrupt(self):
    self.interruptions+=1;self.entered.set()
    while not self.release.is_set():
     try:await self.release.wait()
     except asyncio.CancelledError:continue
  speaker=StubbornSpeaker();self.speaker=speaker;s=self.session();await s.start('确认开始语音会话')
  try:
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',0.03):
    result=await s.stop();self.assertEqual(result['state'],'stopping');self.assertEqual(speaker.interruptions,1);self.assertEqual(speaker.closed,1)
    speaker.release.set();await self.wait_until(lambda:not s._speaker_tasks)
    self.assertEqual((await s.stop())['state'],'stopping');self.assertEqual(speaker.interruptions,1)
  finally:speaker.release.set()
 async def test_ended_recognition_stream_is_an_error(self):
  class EndedRecognizer:
   async def events(self,frames):
    if False:yield None
  s=DuplexSession(EndedRecognizer(),self.model,Synth(),self.speaker,echo_mode='headphones');self.sessions.append(s)
  await s.start('确认开始语音会话');await self.wait_until(lambda:s.status()['state']=='error');self.assertEqual(self.model.calls,[])
 async def test_stop_cancels_inflight_and_closes_output(self):
  self.model=Model(block=True);s=self.session(authorize_turn=lambda:True);await s.start('确认开始语音会话');await s.push_audio(b'\x02\x00')
  await asyncio.wait_for(self.speaker.written.wait(),1);result=await s.stop();self.assertEqual(result['state'],'stopped');self.assertTrue(self.model.cancelled.is_set());self.assertEqual(s.status()['queued_frames'],0)

if __name__=='__main__':unittest.main()
