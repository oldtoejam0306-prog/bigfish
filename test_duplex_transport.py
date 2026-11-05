import asyncio,threading,time,unittest
from bili_workbench.duplex_transport import ThreadedDuplexTransport
from bili_workbench.runtime import Fault

class StubBridge:
 def __init__(self):self.id=None
 async def stop(self,ident):return {'state':'stopped'}

class StubWire:
 def __init__(self):self.bridge=StubBridge();self.calls=0;self.entered=threading.Event();self.finished=threading.Event();self.release=asyncio.Event()
 async def dispatch(self,operation,payload):
  self.calls+=1;self.entered.set();await self.release.wait();self.finished.set();return {'completed':True}

class TransportTests(unittest.TestCase):
 def test_timeout_has_unknown_result_and_never_retries(self):
  wire=StubWire();runner=ThreadedDuplexTransport(wire,timeout=.05)
  try:
   with self.assertRaises(Fault) as error:runner.dispatch('feed',{})
   self.assertIn('结果不明',str(error.exception));self.assertEqual(wire.calls,1)
   self.assertFalse(runner.close());runner.loop.call_soon_threadsafe(wire.release.set)
   self.assertTrue(wire.finished.wait(1))
  finally:
   deadline=time.monotonic()+2
   while not runner.close() and time.monotonic()<deadline:time.sleep(.01)
   self.assertTrue(runner._closed)
 def test_cannot_close_while_start_outcome_is_unknown(self):
  wire=StubWire();runner=ThreadedDuplexTransport(wire,timeout=.05)
  try:
   with self.assertRaises(Fault):runner.dispatch('start',{})
   self.assertFalse(runner.close());self.assertFalse(runner._closed)
   runner.loop.call_soon_threadsafe(wire.release.set);self.assertTrue(wire.finished.wait(1))
  finally:
   deadline=time.monotonic()+2
   while not runner.close() and time.monotonic()<deadline:time.sleep(.01)
   self.assertTrue(runner._closed)
 def test_close_timeout_retains_pending_shutdown_and_blocks_new_dispatch(self):
  class SlowBridge:
   def __init__(self):self.id='fixture';self.calls=0;self.release=asyncio.Event()
   async def stop(self,ident):self.calls+=1;await self.release.wait();return {'state':'stopped'}
  class Wire:
   def __init__(self):self.bridge=SlowBridge()
   async def dispatch(self,*args):return {}
  wire=Wire();runner=ThreadedDuplexTransport(wire,timeout=.05)
  try:
   self.assertFalse(runner.close());self.assertEqual(wire.bridge.calls,1)
   with self.assertRaises(Fault):runner.dispatch('feed',{})
   runner.loop.call_soon_threadsafe(wire.bridge.release.set)
   self.assertTrue(runner.close());self.assertEqual(wire.bridge.calls,1)
  finally:
   if not runner._closed:runner.loop.call_soon_threadsafe(wire.bridge.release.set);runner.close()
 def test_close_keeps_loop_running_when_session_still_stopping(self):
  class Bridge:
   def __init__(self):self.id='fixture';self.calls=0
   async def stop(self,ident):self.calls+=1;return {'state':'stopping' if self.calls==1 else 'stopped'}
  class Wire:
   def __init__(self):self.bridge=Bridge()
   async def dispatch(self,*args):return {'available':True}
  wire=Wire();runner=ThreadedDuplexTransport(wire,timeout=.2)
  try:
   self.assertFalse(runner.close());self.assertTrue(runner.dispatch('poll',{})['available']);self.assertTrue(runner.close())
  finally:
   if not runner._closed:runner.close()

if __name__=='__main__':unittest.main()
