import asyncio,io,json,shutil,struct,subprocess,tempfile,threading,time,unittest,urllib.error,urllib.request,wave
from pathlib import Path
from unittest.mock import patch
from bili_workbench.duplex_adapters import QueueSpeaker
from bili_workbench.duplex_bridge import DuplexBridge
from bili_workbench.duplex_wire import DuplexWire
from bili_workbench.duplex_transport import ThreadedDuplexTransport
from bili_workbench.duplex_factory import build_local_duplex_transport
from bili_workbench.duplex_session import DuplexSession,SpeechStarted,FinalTranscript
from bili_workbench.runtime import Vault
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_duplex_bridge import FakeSession
from test_workbench import FakeHTTP

class EchoSession(FakeSession):
 def __init__(self,speaker):super().__init__();self.speaker=speaker
 async def push_audio(self,frame):self.frames.append(frame);await self.speaker.write(frame)
 async def stop(self):self.state='stopped';await self.speaker.close();return {'state':'stopped','automatic_retry':False}

class FixtureTransport:
 def __init__(self):
  self.speaker=QueueSpeaker();self.session=EchoSession(self.speaker);self.wire=DuplexWire(DuplexBridge(self.session,self.speaker));self.runner=ThreadedDuplexTransport(self.wire)
 def dispatch(self,operation,payload):return self.runner.dispatch(operation,payload)
 def close(self):
  if not self.runner.close():raise RuntimeError('fixture transport did not stop')
  return True

class DuplexHttpTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.transport=FixtureTransport();self.servers=[]
 def tearDown(self):
  for server,thread in self.servers:server.shutdown();server.server_close();thread.join()
  self.transport.close();self.w.close();self.vault.stop();self.tmp.cleanup()
 def server(self,preview=False,transport=True):
  server=Server(('127.0.0.1',0),self.w,preview=preview,duplex_transport=self.transport if transport else None);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread));return server
 def post(self,server,path,payload,auth=True):
  base='http://127.0.0.1:'+str(server.server_port);headers={'Content-Type':'application/json','Origin':base}
  if auth:headers['X-Local-Token']=server.token
  request=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers=headers,method='POST')
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:return json.load(response)
  except urllib.error.HTTPError as error:
   error.read();error.close();raise
 def test_auth_preview_and_unconfigured_are_fail_closed(self):
  for server,auth,code in ((self.server(),False,403),(self.server(preview=True),True,403),(self.server(transport=False),True,503)):
   with self.assertRaises(urllib.error.HTTPError) as error:self.post(server,'/api/duplex/start',{'confirmation':'确认开始语音会话','headphones_confirmed':True},auth)
   self.assertEqual(error.exception.code,code)
  self.assertEqual(self.session_frames(),[]);self.assertEqual(self.http.calls,[])
 def session_frames(self):return list(self.transport.session.frames)
 def test_activation_requires_authentication_and_is_single_use(self):
  server=self.server(transport=False);payload={'speech_confirmation':'确认使用本机语音识别和合成','model_confirmation':'确认语音会话按预算自动调用模型','echo_mode':'headphones'}
  with patch('bili_workbench.duplex_factory.build_local_duplex_transport',return_value=self.transport) as factory:
   with self.assertRaises(urllib.error.HTTPError) as error:self.post(server,'/api/duplex/activate',payload,auth=False)
   self.assertEqual(error.exception.code,403);factory.assert_not_called()
   activated=self.post(server,'/api/duplex/activate',payload);self.assertTrue(activated['activated']);self.assertFalse(activated['microphone_started']);self.assertFalse(activated['model_called'])
   with self.assertRaises(urllib.error.HTTPError) as error:self.post(server,'/api/duplex/activate',payload)
   self.assertEqual(error.exception.code,409);self.assertEqual(factory.call_count,1)
   with self.assertRaises(urllib.error.HTTPError):self.post(server,'/api/duplex/deactivate',{})
   with self.assertRaises(urllib.error.HTTPError):self.post(server,'/api/duplex/start',{'confirmation':'确认开始语音会话','headphones_confirmed':True,'lease':'b'*32})
   self.assertEqual(self.post(server,'/api/duplex/deactivate',{'lease':activated['lease']})['state'],'stopped')
  self.assertEqual(self.http.calls,[])
 def test_unused_activation_expires_without_opening_device(self):
  server=self.server(transport=False);server.duplex_activation_timeout=.05;payload={'speech_confirmation':'确认使用本机语音识别和合成','model_confirmation':'确认语音会话按预算自动调用模型','echo_mode':'headphones'}
  with patch('bili_workbench.duplex_factory.build_local_duplex_transport',return_value=self.transport):
   activated=self.post(server,'/api/duplex/activate',payload);self.assertTrue(activated['activated'])
   deadline=time.monotonic()+2
   while server.duplex_transport is not None and time.monotonic()<deadline:time.sleep(.01)
   self.assertIsNone(server.duplex_transport);self.assertIsNone(server.duplex_lease);self.assertEqual(self.session_frames(),[])
 def test_started_session_is_not_closed_by_activation_timeout(self):
  server=self.server(transport=False);server.duplex_activation_timeout=1.0;payload={'speech_confirmation':'确认使用本机语音识别和合成','model_confirmation':'确认语音会话按预算自动调用模型','echo_mode':'headphones'}
  with patch('bili_workbench.duplex_factory.build_local_duplex_transport',return_value=self.transport):
   activated=self.post(server,'/api/duplex/activate',payload)
   session=self.post(server,'/api/duplex/start',{'confirmation':'确认开始语音会话','headphones_confirmed':True,'lease':activated['lease']})
   time.sleep(1.1);self.assertIs(server.duplex_transport,self.transport)
   self.assertEqual(self.post(server,'/api/duplex/stop',{'id':session['id']})['state'],'stopped')
   self.assertEqual(self.post(server,'/api/duplex/deactivate',{'lease':activated['lease']})['state'],'stopped')
 def test_deactivation_preserves_unknown_stop_until_explicit_recheck(self):
  class PendingTransport:
   def __init__(self):self.calls=0
   def close(self):self.calls+=1;return self.calls>1
  server=self.server(transport=False);transport=PendingTransport();server.duplex_transport=transport
  self.assertEqual(self.post(server,'/api/duplex/deactivate',{})['state'],'stopping');self.assertIs(server.duplex_transport,transport);self.assertEqual(transport.calls,1)
  self.assertEqual(self.post(server,'/api/duplex/deactivate',{})['state'],'stopped');self.assertIsNone(server.duplex_transport);self.assertEqual(transport.calls,2)
 def test_recovery_requires_session_auth_and_explicit_confirmation(self):
  class Transport:
   def __init__(self):self.calls=0
   def close(self):self.calls+=1;return True
  server=self.server(transport=False);transport=Transport();server.duplex_transport=transport;server.duplex_lease='a'*32
  for payload,auth in (({'confirmation':'确认关闭遗留语音会话'},False),({},True),({'confirmation':'确认关闭遗留语音会话','extra':True},True)):
   with self.assertRaises(urllib.error.HTTPError):self.post(server,'/api/duplex/recover',payload,auth)
  self.assertEqual(transport.calls,0);self.assertEqual(self.post(server,'/api/duplex/recover',{'confirmation':'确认关闭遗留语音会话'})['state'],'stopped');self.assertEqual(transport.calls,1);self.assertIsNone(server.duplex_transport)
 def test_service_stop_refuses_when_duplex_still_stopping(self):
  class PendingTransport:
   def close(self):return False
  server=self.server(transport=False);server.duplex_transport=PendingTransport()
  with self.assertRaises(urllib.error.HTTPError) as error:self.post(server,'/api/stop',{})
  self.assertEqual(error.exception.code,400);self.assertIsNotNone(server.duplex_transport)
 def test_service_stop_does_not_hold_database_lock_while_waiting_for_voice_worker(self):
  acquired=threading.Event();threads=[];workbench=self.w
  class FinishingTransport:
   def close(self):
    def finish():
     with workbench.s.lock:acquired.set()
    thread=threading.Thread(target=finish,daemon=True);threads.append(thread);thread.start();return acquired.wait(.5)
  server=self.server(transport=False);server.duplex_transport=FinishingTransport()
  try:
   result=self.post(server,'/api/stop',{});self.assertTrue(result['stopping']);self.assertTrue(acquired.is_set());self.assertIsNone(server.duplex_transport)
  finally:
   for thread in threads:thread.join(2)
 def test_static_client_and_security_headers_are_served_without_execution(self):
  server=self.server();base='http://127.0.0.1:'+str(server.server_port);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  with opener.open(base+'/',timeout=3) as response:
   html=response.read().decode();self.assertIn('<script src="/duplex-client.js"></script>',html);self.assertIn('<script src="/duplex-audio.js"></script>',html);self.assertIn("script-src 'self' 'unsafe-inline'",response.headers['Content-Security-Policy'])
  with opener.open(base+'/duplex-client.js',timeout=3) as response:
   self.assertIn('application/javascript',response.headers['Content-Type']);self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertIn('BigFishDuplexClient',response.read().decode())
  with opener.open(base+'/duplex-audio.js',timeout=3) as response:
   self.assertIn('application/javascript',response.headers['Content-Type']);self.assertIn('BigFishDuplexAudioSession',response.read().decode())
  self.assertEqual(self.session_frames(),[]);self.assertEqual(self.http.calls,[])
 def test_audio_route_does_not_wait_on_workbench_database_lock(self):
  server=self.server();result=self.post(server,'/api/duplex/start',{'confirmation':'确认开始语音会话','headphones_confirmed':True});ident=result['id']
  with self.w.s.lock:
   result=self.post(server,'/api/duplex/feed',{'id':ident,'sequence':1,'pcm_base64':'AQA='})
  self.assertTrue(result['accepted']);self.assertEqual(self.session_frames(),[b'\x01\x00']);self.post(server,'/api/duplex/stop',{'id':ident});self.assertEqual(self.http.calls,[])
 def test_policy_denial_prevents_browser_microphone_and_model(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  server=self.server(transport=False)
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch.object(self.w.model,'chat') as model,patch('bili_workbench.voice_input.readiness',return_value={'ready':False}) as readiness,patch('bili_workbench.voice_input.transcribe_wav') as stt,patch('bili_workbench.system_voice.synthesize') as tts:
   base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_policy_loopback.cjs')
   result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=15)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('policy denial before microphone passed',result.stdout)
   self.assertEqual(readiness.call_count,1);model.assert_not_called();stt.assert_not_called();tts.assert_not_called();self.assertIsNone(server.duplex_transport);self.assertEqual(self.http.calls,[])
 def test_browser_audio_controller_to_lazy_workbench_with_fixture_devices(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  def synth(text,confirmation,**kwargs):
   out=io.BytesIO()
   with wave.open(out,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x01\x00'*320)
   return out.getvalue()
  server=self.server(transport=False)
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('no real credentials')),patch.object(self.w.model,'chat',return_value={'text':'模拟回复','cached':False}) as model,patch('bili_workbench.voice_input.readiness',return_value={'ready':True}),patch('bili_workbench.voice_input.transcribe_wav',return_value={'recognized':True,'text':'模拟语音'}) as stt,patch('bili_workbench.system_voice.synthesize',side_effect=synth) as tts:
   base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_audio_loopback.cjs')
   result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('browser audio to workbench fixture passed',result.stdout)
   self.assertEqual((stt.call_count,model.call_count,tts.call_count),(1,1,1));self.assertIsNone(server.duplex_transport);self.assertEqual(self.http.calls,[])
 def test_factory_to_workbench_server_with_fixture_voice_and_model(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  def synth(text,confirmation,**kwargs):
   out=io.BytesIO()
   with wave.open(out,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x01\x00'*320)
   return out.getvalue()
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('no real credentials')),patch.object(self.w.model,'chat',return_value={'text':'模拟回复','cached':False}) as model,patch('bili_workbench.voice_input.transcribe_wav',return_value={'recognized':True,'text':'模拟语音'}) as stt,patch('bili_workbench.system_voice.synthesize',side_effect=synth) as tts:
   runner=build_local_duplex_transport(self.w,speech_confirmation='确认使用本机语音识别和合成',model_confirmation='确认语音会话按预算自动调用模型',echo_mode='headphones',readiness_probe=lambda:{'ready':True})
   server=Server(('127.0.0.1',0),self.w,duplex_transport=runner);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread))
   try:
    base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_factory_loopback.cjs')
    result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=15)
    self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('factory loopback pipeline passed',result.stdout)
    self.assertEqual((stt.call_count,model.call_count,tts.call_count),(1,1,1));self.assertEqual(self.http.calls,[])
   finally:self.assertTrue(runner.close())
 def test_browser_voice_uses_guarded_sse_model_and_settles_fixture_usage(self):
  from test_model_stream import Opener
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://api.deepseek.com','model':'deepseek-flash','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  self.w.s.gate=lambda *args,**kwargs:None;self.http.opener=Opener();server=self.server(transport=False)
  def synth(text,confirmation,**kwargs):
   out=io.BytesIO()
   with wave.open(out,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x01\x00'*320)
   return out.getvalue()
  with patch.object(self.w.s.vault,'read',return_value={'api_key':'fixture-not-real-key'}),patch('bili_workbench.deepseek_test.require_window'),patch('bili_workbench.voice_input.readiness',return_value={'ready':True}),patch('bili_workbench.voice_input.transcribe_wav',return_value={'recognized':True,'text':'模拟语音'}),patch('bili_workbench.system_voice.synthesize',side_effect=synth):
   base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_audio_loopback.cjs');result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertEqual(len(self.http.opener.calls),1);self.assertEqual(self.http.calls,[]);self.assertIsNone(server.duplex_transport)
   charge=self.w.s.db.execute('SELECT state,actual FROM charges').fetchone();self.assertEqual((charge['state'],charge['actual']),('settled',36));self.assertEqual(len(self.w.local_chat.history()['messages']),2)
 def test_optional_native_pcm_adapter_is_wired_to_browser_with_fixture_provider(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  calls=[];server=self.server(transport=False)
  async def render(text,confirmation):calls.append((text,confirmation));yield b'\x01\x00'*1600
  def factory(workbench,**options):return build_local_duplex_transport(workbench,**options,speech_backend='windows-pcm-stream')
  with patch('bili_workbench.duplex_factory.build_local_duplex_transport',side_effect=factory),patch('bili_workbench.native_voice_stream.stream_pcm',new=render),patch('bili_workbench.voice_input.readiness',return_value={'ready':True}),patch('bili_workbench.voice_input.transcribe_wav',return_value={'recognized':True,'text':'模拟语音'}),patch.object(self.w.model,'chat',return_value={'text':'完整模拟回答。','cached':False}),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_audio_loopback.cjs');result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertEqual(calls,[('完整模拟回答',True)]);self.assertEqual(self.http.calls,[]);self.assertIsNone(server.duplex_transport)
 def test_continuous_recognition_pipe_to_browser_uses_one_fixture_host_and_stops_it(self):
  from bili_workbench.native_recognizer import NativeContinuousRecognizer as Native
  from test_native_recognizer import PipeChild
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
  root=Path(self.tmp.name)/'fake-windows';exe=root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe';exe.parent.mkdir(parents=True);exe.touch();children=[]
  async def create(*args,**kwargs):
   def recognize(child,frame):
    if len(child.sent)==1:child.emit('speech',utterance=1);child.emit('final',utterance=1,text='模拟持续识别')
   child=PipeChild(recognize);children.append(child);return child
  class Injected(Native):
   def __init__(self,**options):super().__init__(**options,process_factory=create,platform='nt',system_root=root)
  def factory(workbench,**options):return build_local_duplex_transport(workbench,**options,recognition_backend='windows-continuous')
  def synth(text,confirmation,**kwargs):
   out=io.BytesIO()
   with wave.open(out,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x01\x00'*320)
   return out.getvalue()
  server=self.server(transport=False)
  with patch('bili_workbench.duplex_factory.build_local_duplex_transport',side_effect=factory),patch('bili_workbench.native_recognizer.NativeContinuousRecognizer',Injected),patch('bili_workbench.voice_input.readiness',return_value={'ready':True}),patch('bili_workbench.voice_input.transcribe_wav') as segmented,patch('bili_workbench.system_voice.synthesize',side_effect=synth),patch.object(self.w.model,'chat',return_value={'text':'完整模拟回答。','cached':False}) as model,patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_audio_loopback.cjs');result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertEqual(len(children),1);self.assertEqual(children[0].kills,1);self.assertEqual(children[0].returncode,-9);self.assertTrue(children[0].sent);self.assertEqual(model.call_count,1);segmented.assert_not_called();self.assertEqual(self.http.calls,[]);self.assertIsNone(server.duplex_transport)
 def test_node_browser_client_to_actual_server_with_fixture_audio(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  server=self.server();base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_loopback.cjs')
  result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('loopback browser transport passed',result.stdout)
  self.assertEqual(self.session_frames(),[b'\x01\x00',b'\x02\x00']);self.assertEqual(self.http.calls,[])

 def test_incremental_fixture_output_and_barge_in_cross_browser_and_http(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  calls=[];cancelled=threading.Event();first_waiting=threading.Event()
  class Recognizer:
   async def events(self,frames):
    async for frame in frames:
     marker=struct.unpack_from('<h',frame)[0]
     yield SpeechStarted();yield FinalTranscript('first' if marker<5000 else 'second')
  class StreamingModel:
   async def stream_reply(self,text):
    calls.append(text)
    try:
     yield text
     if text=='first':first_waiting.set();await asyncio.Event().wait()
    finally:
     if text=='first':cancelled.set()
  class StreamingSynth:
   async def stream_pcm(self,texts):
    async for text in texts:yield struct.pack('<h',111 if text=='first' else 222)*1600
  speaker=QueueSpeaker();session=DuplexSession(Recognizer(),StreamingModel(),StreamingSynth(),speaker,authorize_turn=lambda:True,echo_mode='headphones')
  runner=ThreadedDuplexTransport(DuplexWire(DuplexBridge(session,speaker)));server=self.server(transport=False)
  try:
   with patch('bili_workbench.duplex_factory.build_local_duplex_transport',return_value=runner),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch('bili_workbench.voice_input.transcribe_wav') as stt,patch('bili_workbench.system_voice.synthesize') as tts,patch.object(self.w.model,'chat') as real_model:
    base='http://127.0.0.1:'+str(server.server_port);script=Path(__file__).with_name('test_duplex_interrupt_loopback.cjs')
    result=subprocess.run([node,str(script),base,server.token],capture_output=True,text=True,timeout=20)
    self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('streaming fixture first chunk, barge-in and shutdown passed',result.stdout)
    self.assertEqual(calls,['first','second']);self.assertTrue(first_waiting.is_set());self.assertTrue(cancelled.is_set());self.assertIsNone(server.duplex_transport);self.assertEqual(self.http.calls,[])
    stt.assert_not_called();tts.assert_not_called();real_model.assert_not_called()
  finally:self.assertTrue(runner.close())

if __name__=='__main__':unittest.main()
