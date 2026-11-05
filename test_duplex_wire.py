import asyncio,base64,unittest
from bili_workbench.duplex_wire import DuplexWire
from bili_workbench.duplex_bridge import DuplexBridge
from bili_workbench.duplex_adapters import QueueSpeaker
from bili_workbench.runtime import Fault
from test_duplex_bridge import FakeSession

class WireTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.session=FakeSession();self.speaker=QueueSpeaker();self.wire=DuplexWire(DuplexBridge(self.session,self.speaker))
  self.id=(await self.wire.dispatch('start',{'confirmation':'确认开始语音会话','headphones_confirmed':True}))['id']
 async def test_json_input_is_ordered_idempotent_and_bounded(self):
  data={'id':self.id,'sequence':1,'pcm_base64':base64.b64encode(b'\x01\x00').decode()}
  self.assertFalse((await self.wire.dispatch('feed',data))['duplicate']);self.assertTrue((await self.wire.dispatch('feed',data))['duplicate']);self.assertEqual(self.session.frames,[b'\x01\x00'])
  for value in ('not+base64!', 'A'*4272,base64.b64encode(b'x').decode()):
   with self.assertRaises(Fault):await self.wire.dispatch('feed',dict(data,sequence=2,pcm_base64=value))
  with self.assertRaises(Fault):await self.wire.dispatch('feed',dict(data,extra='unexpected'))
  self.assertEqual(len(self.session.frames),1)
 async def test_output_is_json_safe_and_preserves_ack_and_generation(self):
  await self.speaker.write(b'\x01\x00');first=await self.wire.dispatch('poll',{'id':self.id,'ack':0})
  self.assertEqual(base64.b64decode(first['output']['pcm_base64']),b'\x01\x00')
  repeated=await self.wire.dispatch('poll',{'id':self.id,'ack':0});self.assertEqual(first['output'],repeated['output'])
  await self.speaker.interrupt();result=await self.wire.dispatch('poll',{'id':self.id,'ack':0});self.assertIsNone(result['output']);self.assertNotEqual(result['generation'],first['generation'])
 async def test_status_only_poll_does_not_remove_audio(self):
  await self.speaker.write(b'\x01\x00');result=await self.wire.dispatch('poll',{'id':self.id,'ack':0,'status_only':True})
  self.assertIsNone(result['output']);self.assertEqual(self.speaker.queue.qsize(),1)
  self.assertIsNotNone((await self.wire.dispatch('poll',{'id':self.id,'ack':0}))['output'])
 async def test_unknown_operation_wrong_id_and_extra_fields_fail(self):
  for operation,payload in [('unknown',{}),('poll',{'id':'无效','ack':0}),('poll',{'id':self.id,'ack':0,'secret':'ignored'}),('stop',{'id':self.id,'unexpected':True})]:
   with self.assertRaises(Fault):await self.wire.dispatch(operation,payload)
  self.assertEqual((await self.wire.dispatch('stop',{'id':self.id}))['state'],'stopped')

if __name__=='__main__':unittest.main()
