import asyncio,struct,unittest
from bili_workbench.duplex_bridge import DuplexBridge
from bili_workbench.duplex_session import DuplexSession
from bili_workbench.duplex_adapters import EnergySegmentRecognizer,LocalChatModelAdapter,QueueSpeaker
from bili_workbench.runtime import Fault

class FakeSession:
 def __init__(self):self.frames=[];self.state='idle';self.echo_mode='headphones'
 async def start(self,confirmation):
  if confirmation!='确认开始语音会话':raise Fault('缺少确认')
  self.state='listening';return {'state':self.state}
 async def push_audio(self,frame):self.frames.append(frame)
 async def stop(self):self.state='stopped';return {'state':self.state,'automatic_retry':False}
 def status(self):return {'state':self.state}

class BridgeTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.session=FakeSession();self.speaker=QueueSpeaker(frames=2);self.bridge=DuplexBridge(self.session,self.speaker);self.id=(await self.bridge.start('确认开始语音会话',headphones_confirmed=True))['id']
 async def test_echo_requires_explicit_headphones_or_reported_browser_setting(self):
  session=FakeSession();speaker=QueueSpeaker();bridge=DuplexBridge(session,speaker)
  with self.assertRaises(Fault):await bridge.start('确认开始语音会话')
  self.assertEqual(session.state,'idle');session.echo_mode='browser-echo-cancellation'
  with self.assertRaises(Fault):await bridge.start('确认开始语音会话',browser_echo_reported=False)
  result=await bridge.start('确认开始语音会话',browser_echo_reported=True)
  self.assertFalse(result['echo_effectiveness_verified']);self.assertEqual(result['echo_mode'],'browser-echo-cancellation')
 async def test_input_replay_order_and_session_checks(self):
  a,b=await asyncio.gather(self.bridge.feed(self.id,1,b'\x01\x00'),self.bridge.feed(self.id,1,b'\x01\x00'))
  self.assertEqual(sorted([a['duplicate'],b['duplicate']]),[False,True]);self.assertEqual(self.session.frames,[b'\x01\x00'])
  for ident,seq,frame in [(self.id,1,b'\x02\x00'),(self.id,3,b'\x01\x00'),('无效',2,b'\x01\x00'),(self.id,True,b'\x01\x00'),(self.id,2,b'x')]:
   with self.assertRaises(Fault):await self.bridge.feed(ident,seq,frame)
  self.assertEqual(len(self.session.frames),1)
 async def test_poll_acknowledgement_and_duplicate_are_explicit(self):
  await self.speaker.write(b'\x01\x00');await self.speaker.write(b'\x02\x00')
  first=await self.bridge.poll(self.id,0);repeat=await self.bridge.poll(self.id,0)
  self.assertEqual(first['output'],repeat['output']);self.assertTrue(first['client_must_deduplicate'])
  second=await self.bridge.poll(self.id,first['output']['sequence']);self.assertEqual(second['output']['pcm'],b'\x02\x00')
  self.assertIsNone((await self.bridge.poll(self.id,second['output']['sequence']))['output'])
  with self.assertRaises(Fault):await self.bridge.poll(self.id,999)
 async def test_status_only_acks_without_dequeuing_next_audio(self):
  await self.speaker.write(b'\x01\x00');await self.speaker.write(b'\x02\x00')
  first=await self.bridge.poll(self.id,0);status=await self.bridge.poll(self.id,first['output']['sequence'],status_only=True)
  self.assertIsNone(status['output']);self.assertEqual(self.speaker.queue.qsize(),1)
  next_frame=await self.bridge.poll(self.id,first['output']['sequence']);self.assertEqual(next_frame['output']['pcm'],b'\x02\x00')
  with self.assertRaises(Fault):await self.bridge.poll(self.id,first['output']['sequence'],status_only='yes')
 async def test_interruption_invalidates_unacknowledged_audio(self):
  await self.speaker.write(b'\x01\x00');first=await self.bridge.poll(self.id,0);await self.speaker.interrupt()
  changed=await self.bridge.poll(self.id,0);self.assertNotEqual(changed['generation'],first['generation']);self.assertIsNone(changed['output'])
  await self.speaker.write(b'\x02\x00');new=await self.bridge.poll(self.id,0);self.assertNotEqual(new['output']['sequence'],first['output']['sequence']);self.assertEqual(new['output']['pcm'],b'\x02\x00')
 async def test_inactivity_stops_or_heartbeat_keeps_session_alive(self):
  session=FakeSession();speaker=QueueSpeaker();bridge=DuplexBridge(session,speaker,inactivity_seconds=.08)
  ident=(await bridge.start('确认开始语音会话',headphones_confirmed=True))['id']
  for _ in range(3):await asyncio.sleep(.03);await bridge.poll(ident,0);self.assertEqual(session.state,'listening')
  await asyncio.sleep(.12);self.assertEqual(session.state,'stopped')
  with self.assertRaises(Fault):await bridge.feed(ident,1,b'\x01\x00')
 async def test_invalid_poll_does_not_extend_idle_session(self):
  session=FakeSession();speaker=QueueSpeaker();bridge=DuplexBridge(session,speaker,inactivity_seconds=.08)
  ident=(await bridge.start('确认开始语音会话',headphones_confirmed=True))['id']
  for _ in range(4):
   await asyncio.sleep(.03)
   with self.assertRaises(Fault):await bridge.poll(ident,999)
  self.assertEqual(session.state,'stopped')
 async def test_stop_invalidates_output_and_rejects_more_audio(self):
  await self.speaker.write(b'\x01\x00');first=await self.bridge.poll(self.id,0);self.assertIsNotNone(first['output'])
  self.assertEqual((await self.bridge.stop(self.id))['state'],'stopped');self.assertIsNone((await self.bridge.poll(self.id,0))['output'])
  with self.assertRaises(Fault):await self.bridge.feed(self.id,1,b'\x01\x00')
  with self.assertRaises(Fault):await self.bridge.start('确认开始语音会话',headphones_confirmed=True)

class BridgeChainTests(unittest.IsolatedAsyncioTestCase):
 async def test_simulated_stream_through_real_bridge_without_network(self):
  class Chat:
   def __init__(self):self.calls=[]
   def send(self,request_id,text,include_memory,include_knowledge,**kwargs):self.calls.append(text);return {'status':'completed','reply':'模拟回复'}
  class Synth:
   async def stream_pcm(self,texts):
    async for text in texts:yield b'\x00\x01'*len(text)
  async def transcribe(data):return '模拟说话'
  chat=Chat();speaker=QueueSpeaker();session=DuplexSession(EnergySegmentRecognizer(transcribe),LocalChatModelAdapter(chat),Synth(),speaker,authorize_turn=lambda:True,echo_mode='headphones');bridge=DuplexBridge(session,speaker)
  ident=(await bridge.start('确认开始语音会话',headphones_confirmed=True))['id']
  try:
   voiced=struct.pack('<h',2000)*320;silence=b'\x00\x00'*320
   for i,frame in enumerate([voiced,voiced]+[silence]*30,1):await bridge.feed(ident,i,frame);await asyncio.sleep(0)
   async def get_output():
    while True:
     result=await bridge.poll(ident,0)
     if result['output']:return result
     await asyncio.sleep(.005)
   result=await asyncio.wait_for(get_output(),2);self.assertEqual(chat.calls,['模拟说话']);self.assertGreater(len(result['output']['pcm']),0)
  finally:await bridge.stop(ident)

if __name__=='__main__':unittest.main()
