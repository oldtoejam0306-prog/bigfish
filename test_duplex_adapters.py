import asyncio,base64,io,struct,tempfile,threading,unittest,wave
from unittest.mock import patch
from bili_workbench.duplex_adapters import LocalChatModelAdapter,EnergySegmentRecognizer,QueueSpeaker,WindowsBufferedTranscriber,WindowsBufferedSynthesizer,WindowsSegmentedSynthesizer
from bili_workbench.duplex_session import DuplexSession,SpeechStarted,FinalTranscript
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from test_workbench import FakeHTTP

class FakeChat:
 def __init__(self):self.requests=[]
 def send(self,request_id,text,include_memory,include_knowledge,**kwargs):
  self.requests.append((request_id,text,include_memory,include_knowledge))
  return {'status':'completed','reply':'模拟回复'}

async def collect(iterator):return [item async for item in iterator]
async def frames(*items):
 for item in items:yield item

def pcm(value,samples=320):return struct.pack('<h',value)*samples

class Synth:
 async def stream_pcm(self,texts):
  async for text in texts:yield b'\x00\x01'*len(text)

class DuplexAdapterTests(unittest.IsolatedAsyncioTestCase):
 async def test_local_chat_adapter_uses_selected_options_and_unique_ids(self):
  chat=FakeChat();adapter=LocalChatModelAdapter(chat,include_memory=True)
  self.assertEqual(await collect(adapter.stream_reply('你好')),['模拟回复'])
  self.assertEqual(await collect(adapter.stream_reply('再见')),['模拟回复'])
  self.assertNotEqual(chat.requests[0][0],chat.requests[1][0]);self.assertEqual(chat.requests[0][1:],('你好',True,False))
 async def test_cancel_waits_for_inflight_chat_and_does_not_emit_reply(self):
  class BlockingChat(FakeChat):
   def __init__(self):super().__init__();self.started=threading.Event();self.release=threading.Event()
   def send(self,*args,**kwargs):
    self.requests.append(args);self.started.set();self.release.wait(2);return {'status':'completed','reply':'不应播放'}
  chat=BlockingChat();adapter=LocalChatModelAdapter(chat);output=[]
  async def consume():
   async for chunk in adapter.stream_reply('模拟'):output.append(chunk)
  task=asyncio.create_task(consume())
  try:
   self.assertTrue(await asyncio.to_thread(chat.started.wait,1));task.cancel();await asyncio.sleep(.03);self.assertFalse(task.done())
   task.cancel();await asyncio.sleep(.03);self.assertFalse(task.done());chat.release.set()
   with self.assertRaises(asyncio.CancelledError):await task
   self.assertEqual(len(chat.requests),1);self.assertEqual(output,[])
  finally:chat.release.set()
 async def test_unknown_chat_result_is_not_retried(self):
  class UnknownChat(FakeChat):
   def send(self,*args,**kwargs):self.requests.append(args);return {'status':'already_recorded','previous_state':'uncertain'}
  chat=UnknownChat();adapter=LocalChatModelAdapter(chat)
  with self.assertRaises(Fault):await collect(adapter.stream_reply('模拟'))
  self.assertEqual(len(chat.requests),1)
 async def test_stream_model_cancel_signal_reaches_inflight_worker_without_false_completion(self):
  started=threading.Event();release=threading.Event();controls=[];output=[]
  class Chat:
   def send(self,*args,**kwargs):
    control=kwargs['stream_control'];controls.append(control);started.set();release.wait(2);return {'status':'completed','reply':'不应输出'}
  async def run():
   async for piece in LocalChatModelAdapter(Chat(),stream_model=True).stream_reply('模拟'):output.append(piece)
  task=asyncio.create_task(run())
  try:
   self.assertTrue(await asyncio.to_thread(started.wait,1));task.cancel();await asyncio.sleep(.03);self.assertTrue(controls[0].cancelled());self.assertFalse(task.done());release.set()
   with self.assertRaises(asyncio.CancelledError):await task
   self.assertEqual(output,[]);self.assertEqual(len(controls),1)
  finally:release.set()
 async def test_default_synthesis_cancel_signal_waits_for_worker_and_never_emits_audio(self):
  started=threading.Event();release=threading.Event();checks=[]
  def fake(text,confirmation,*,cancel_check):checks.append(cancel_check);started.set();release.wait(2);raise Fault('模拟取消')
  async def text():yield '模拟。'
  with patch('bili_workbench.system_voice.synthesize',side_effect=fake):
   task=asyncio.create_task(collect(WindowsBufferedSynthesizer().stream_pcm(text())))
   try:
    self.assertTrue(await asyncio.to_thread(started.wait,1));task.cancel();await asyncio.sleep(.03);self.assertTrue(checks[0]());self.assertFalse(task.done());release.set()
    with self.assertRaises(asyncio.CancelledError):await task
   finally:release.set()
 async def test_windows_transcriber_adapts_pcm_to_wav_with_fixture(self):
  seen=[]
  def fake(encoded):
   with wave.open(io.BytesIO(base64.b64decode(encoded)),'rb') as wav:
    seen.append((wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.readframes(wav.getnframes())))
   return {'recognized':True,'text':'模拟转写'}
  transcriber=WindowsBufferedTranscriber(fake);data=pcm(1234)
  self.assertEqual(await transcriber(data),'模拟转写');self.assertEqual(seen,[(1,2,16000,data)])
  with self.assertRaises(Fault):await transcriber(b'x')
  self.assertEqual(len(seen),1)
 async def test_windows_synth_resamples_fixture_stereo_wav_to_pcm(self):
  calls=[]
  def fake(text,confirmation):
   calls.append((text,confirmation));out=io.BytesIO()
   with wave.open(out,'wb') as wav:
    wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(struct.pack('<hh',1000,-500)*2400)
   return out.getvalue()
  async def text():yield '模拟';yield '朗读'
  chunks=await collect(WindowsBufferedSynthesizer(fake).stream_pcm(text()))
  self.assertEqual(calls,[('模拟朗读',True)]);self.assertEqual(sum(map(len,chunks)),3200);self.assertEqual(struct.unpack('<h',chunks[0][:2])[0],250)
 async def test_windows_synth_rejects_invalid_data_or_oversize_text(self):
  calls=[]
  def fake(text,confirmation):calls.append(text);return b'not a WAV'
  async def short():yield '模拟'
  with self.assertRaises(Fault):await collect(WindowsBufferedSynthesizer(fake).stream_pcm(short()))
  async def long():yield '字'*301
  with self.assertRaises(Fault):await collect(WindowsBufferedSynthesizer(fake).stream_pcm(long()))
  self.assertEqual(calls,['模拟'])
 async def test_segmented_speech_waits_for_complete_reply_before_first_audio(self):
  calls=[];ready=asyncio.Event()
  def fake(text,confirmation):
   calls.append(text);out=io.BytesIO()
   with wave.open(out,'wb') as wav:
    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm(300,320))
   return out.getvalue()
  async def text():
   yield '第一句。';await ready.wait();yield '第二句。'
  stream=WindowsSegmentedSynthesizer(fake).stream_pcm(text())
  task=asyncio.create_task(anext(stream));await asyncio.sleep(.03);self.assertFalse(task.done());self.assertEqual(calls,[])
  ready.set();first=await asyncio.wait_for(task,2);self.assertEqual(len(first),640);rest=await collect(stream);self.assertEqual(len(rest),1);self.assertEqual(calls,['第一句。','第二句。'])
 async def test_late_invalid_text_or_producer_failure_never_synthesizes_prefix(self):
  calls=[]
  def fake(text,confirmation):calls.append(text);return b''
  async def invalid():yield '你好。';yield '我是';yield '真实的人类。'
  async def failed():yield '你好。';raise Fault('模拟后续流失败')
  async def long():yield '你好。';yield '字'*1001
  for source in (invalid(),failed(),long()):
   with self.assertRaises(Fault):await collect(WindowsSegmentedSynthesizer(fake).stream_pcm(source))
  self.assertEqual(calls,[])
 async def test_cancel_during_reply_validation_never_opens_synthesizer(self):
  calls=[];waiting=asyncio.Event();release=asyncio.Event()
  def fake(text,confirmation):calls.append(text);return b''
  async def text():yield '你好。';waiting.set();await release.wait();yield '之后。'
  task=asyncio.create_task(collect(WindowsSegmentedSynthesizer(fake).stream_pcm(text())))
  await waiting.wait();task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(calls,[])
 async def test_empty_delta_flood_is_bounded_before_synthesis(self):
  calls=[]
  def fake(text,confirmation):calls.append(text);return b''
  async def text():
   for i in range(4097):yield ''
  with self.assertRaises(Fault):await collect(WindowsSegmentedSynthesizer(fake).stream_pcm(text()))
  self.assertEqual(calls,[])
 async def test_segmented_speech_rejects_long_input_before_synthesis(self):
  calls=[]
  def fake(text,confirmation):calls.append(text);return b''
  async def text():yield '字'*1001
  with self.assertRaises(Fault):await collect(WindowsSegmentedSynthesizer(fake).stream_pcm(text()))
  self.assertEqual(calls,[])
 async def test_energy_segmentation_yields_start_and_final_without_recording(self):
  received=[]
  async def transcribe(data):received.append(data);return '模拟识别'
  recognizer=EnergySegmentRecognizer(transcribe)
  events=await collect(recognizer.events(frames(pcm(0),pcm(2000),pcm(2000),*[pcm(0)]*30)))
  self.assertEqual([type(e) for e in events],[SpeechStarted,FinalTranscript]);self.assertEqual(events[-1].text,'模拟识别');self.assertEqual(len(received),1);self.assertLessEqual(len(received[0]),320000)
 async def test_segment_rejects_oversize_without_transcribing(self):
  calls=[]
  async def transcribe(data):calls.append(data);return 'test'
  recognizer=EnergySegmentRecognizer(transcribe,max_seconds=1)
  chunks=[pcm(2000,1002)]*17
  with self.assertRaises(Fault):await collect(recognizer.events(frames(*chunks)))
  self.assertEqual(calls,[])
 async def test_queue_interrupt_discards_pending_audio_and_close_wakes_reader(self):
  speaker=QueueSpeaker(frames=1);await speaker.write(b'\x00\x01');await speaker.interrupt();self.assertTrue(speaker.queue.empty())
  reader=asyncio.create_task(speaker.read());await asyncio.sleep(0);await speaker.close()
  with self.assertRaises(Fault):await asyncio.wait_for(reader,1)
  with self.assertRaises(Fault):await speaker.write(b'\x00\x01')
 async def test_blocked_write_never_delivers_after_interrupt(self):
  speaker=QueueSpeaker(frames=1);await speaker.write(b'\x01\x00');writer=asyncio.create_task(speaker.write(b'\x02\x00'));await asyncio.sleep(0);await speaker.interrupt()
  with self.assertRaises(Fault):await writer
  # Drain any invalidated frame before sending fresh audio.
  reader=asyncio.create_task(speaker.read());await asyncio.sleep(0)
  await speaker.write(b'\x03\x00')
  self.assertEqual(await asyncio.wait_for(reader,1),b'\x03\x00')
  await speaker.close()
 async def test_cancel_while_waiting_for_chat_lock_never_calls_model(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http);w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
   try:
    adapter=LocalChatModelAdapter(w.local_chat)
    with patch.object(w.model,'chat') as model:
     with w.s.lock:
      task=asyncio.create_task(collect(adapter.stream_reply('模拟')))
      await asyncio.sleep(.08);task.cancel();await asyncio.sleep(.03);self.assertFalse(task.done())
     with self.assertRaises(asyncio.CancelledError):await task
     model.assert_not_called();self.assertEqual(w.local_chat.history()['messages'],[]);self.assertEqual(http.calls,[])
   finally:w.close()
 async def test_real_local_chat_budget_guard_is_reused_with_fixture_model(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   try:
    adapter=LocalChatModelAdapter(w.local_chat)
    with patch.object(w.model,'chat',return_value={'text':'模拟回复','cached':False}) as model:
     with self.assertRaises(Fault):await collect(adapter.stream_reply('模拟'))
     model.assert_not_called()
     w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
     self.assertEqual(await collect(adapter.stream_reply('模拟')),['模拟回复']);self.assertEqual(model.call_count,1)
     self.assertEqual(len(w.local_chat.history()['messages']),2)
    self.assertEqual(http.calls,[])
   finally:w.close()
 async def test_session_stop_reports_pending_chat_worker_until_it_finishes(self):
  class BlockingChat(FakeChat):
   def __init__(self):super().__init__();self.started=threading.Event();self.release=threading.Event()
   def send(self,*args,**kwargs):self.requests.append(args);self.started.set();self.release.wait(2);return {'status':'completed','reply':'不应播放'}
  class Recognizer:
   async def events(self,source):
    async for frame in source:yield FinalTranscript('模拟语音')
  chat=BlockingChat();speaker=QueueSpeaker();session=DuplexSession(Recognizer(),LocalChatModelAdapter(chat),Synth(),speaker,authorize_turn=lambda:True,echo_mode='headphones')
  try:
   await session.start('确认开始语音会话');await session.push_audio(b'\x01\x00')
   self.assertTrue(await asyncio.to_thread(chat.started.wait,1))
   with patch('bili_workbench.duplex_session._SHUTDOWN_TIMEOUT',0.03):
    result=await session.stop();self.assertEqual(result['state'],'stopping');self.assertEqual(len(chat.requests),1)
    chat.release.set()
    async def wait_worker():
     while not session._response.done():await asyncio.sleep(.001)
    await asyncio.wait_for(wait_worker(),2);self.assertEqual((await session.stop())['state'],'stopped')
    self.assertTrue(speaker.closed)
  finally:chat.release.set();await session.stop()
 async def test_mock_audio_to_mock_local_chat_to_output(self):
  recorded=[]
  async def transcribe(data):recorded.append(len(data));return '模拟语音'
  chat=FakeChat();speaker=QueueSpeaker();session=DuplexSession(EnergySegmentRecognizer(transcribe),LocalChatModelAdapter(chat),Synth(),speaker,authorize_turn=lambda:True,echo_mode='headphones')
  try:
   await session.start('确认开始语音会话')
   for frame in [pcm(2000),pcm(2000)]+[pcm(0)]*30:
    await session.push_audio(frame);await asyncio.sleep(0)
   output=await asyncio.wait_for(speaker.read(),2)
   self.assertGreater(len(output),0);self.assertEqual(chat.requests[0][1],'模拟语音');self.assertEqual(len(recorded),1)
  finally:await session.stop()

class FullFixtureChainTests(unittest.IsolatedAsyncioTestCase):
 async def test_pcm_to_existing_chat_memory_and_back_to_pcm_without_network(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   def fake_stt(encoded):return {'recognized':True,'text':'我喜欢茉莉花'}
   def fake_tts(text,confirmation):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
     wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm(300,1600))
    return out.getvalue()
   w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
   w.growth.controls().remember(Scope.owner(),'主人喜欢茉莉花','主人确认',1.0)
   speaker=QueueSpeaker();session=DuplexSession(EnergySegmentRecognizer(WindowsBufferedTranscriber(fake_stt)),LocalChatModelAdapter(w.local_chat,include_memory=True),WindowsSegmentedSynthesizer(fake_tts),speaker,authorize_turn=lambda:True,echo_mode='headphones')
   try:
    with patch.object(w.s.vault,'read',side_effect=AssertionError('no real credentials')),patch.object(w.model,'chat',return_value={'text':'我记得你喜欢茉莉花','cached':False}) as model:
     await session.start('确认开始语音会话')
     for frame in [pcm(2000),pcm(2000)]+[pcm(0)]*30:await session.push_audio(frame);await asyncio.sleep(0)
     result=await asyncio.wait_for(speaker.read(),3)
     self.assertEqual(len(result),3200);self.assertTrue(any('茉莉花' in m['content'] for m in model.call_args.args[3]));self.assertEqual(len(w.local_chat.history()['messages']),2)
     self.assertEqual(http.calls,[])
   finally:await session.stop();w.close()
 async def test_zero_budget_blocks_full_fixture_chain_before_model(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   def fake_stt(encoded):return {'recognized':True,'text':'模拟语音'}
   speaker=QueueSpeaker();session=DuplexSession(EnergySegmentRecognizer(WindowsBufferedTranscriber(fake_stt)),LocalChatModelAdapter(w.local_chat),WindowsBufferedSynthesizer(lambda *_:b'never'),speaker,authorize_turn=lambda:True,echo_mode='headphones')
   try:
    with patch.object(w.model,'chat') as model:
     await session.start('确认开始语音会话')
     for frame in [pcm(2000),pcm(2000)]+[pcm(0)]*30:await session.push_audio(frame);await asyncio.sleep(0)
     async def wait_error():
      while session.status()['state']!='error':await asyncio.sleep(.001)
     await asyncio.wait_for(wait_error(),3);model.assert_not_called();self.assertEqual(http.calls,[])
   finally:await session.stop();w.close()

if __name__=='__main__':unittest.main()
