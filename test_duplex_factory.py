import tempfile,unittest
from unittest.mock import patch
from bili_workbench.duplex_factory import build_local_duplex_transport
from bili_workbench.runtime import Vault,Fault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class DuplexFactoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
  self.args={'speech_confirmation':'确认使用本机语音识别和合成','model_confirmation':'确认语音会话按预算自动调用模型','echo_mode':'headphones'}
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def configure(self):self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','price_version':'fixture-CNY','input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000})
 def test_consent_and_zero_budget_fail_before_readiness_or_model(self):
  with patch('bili_workbench.duplex_factory.ThreadedDuplexTransport') as transport,patch.object(self.w.model,'chat') as model:
   for overrides in ({'speech_confirmation':''},{'model_confirmation':''},{'include_memory':True},{'include_knowledge':True}):
    with self.assertRaises(Fault):build_local_duplex_transport(self.w,**dict(self.args,**overrides))
   with self.assertRaises(Fault):build_local_duplex_transport(self.w,**self.args)
   transport.assert_not_called();model.assert_not_called();self.assertEqual(self.http.calls,[])
 def test_blocked_voice_condition_never_starts_transport(self):
  self.configure()
  with patch('bili_workbench.duplex_factory.ThreadedDuplexTransport') as transport:
   with self.assertRaises(Fault):build_local_duplex_transport(self.w,**self.args,readiness_probe=lambda:{'ready':False})
   transport.assert_not_called();self.assertEqual(self.http.calls,[])
 def test_confirmed_fixture_assembles_but_does_not_open_device_or_model(self):
  self.configure()
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('no real credentials')),patch.object(self.w.model,'chat') as model,patch('bili_workbench.voice_input.transcribe_wav') as stt,patch('bili_workbench.system_voice.synthesize') as tts:
   runner=build_local_duplex_transport(self.w,**self.args,readiness_probe=lambda:{'ready':True})
   try:
    started=runner.dispatch('start',{'confirmation':'确认开始语音会话','headphones_confirmed':True})
    self.assertFalse(started['microphone_started']);self.assertFalse(started['network_started'])
    self.assertTrue(runner.dispatch('feed',{'id':started['id'],'sequence':1,'pcm_base64':'AAA='})['accepted'])
    self.assertEqual(runner.dispatch('stop',{'id':started['id']})['state'],'stopped')
    model.assert_not_called();stt.assert_not_called();tts.assert_not_called();self.assertEqual(self.http.calls,[])
   finally:self.assertTrue(runner.close())
 def test_native_pcm_backend_is_explicit_and_does_not_start_on_assembly(self):
  from bili_workbench.duplex_adapters import WindowsPCMStreamSynthesizer,WindowsSegmentedSynthesizer
  self.configure()
  with patch('bili_workbench.native_voice_stream.stream_pcm') as render:
   default=build_local_duplex_transport(self.w,**self.args,readiness_probe=lambda:{'ready':True})
   native=build_local_duplex_transport(self.w,**self.args,speech_backend='windows-pcm-stream',readiness_probe=lambda:{'ready':True})
   try:
    self.assertIsInstance(default.wire.bridge.session.synthesizer,WindowsSegmentedSynthesizer);self.assertIsInstance(native.wire.bridge.session.synthesizer,WindowsPCMStreamSynthesizer);render.assert_not_called()
   finally:self.assertTrue(default.close());self.assertTrue(native.close())
 def test_continuous_recognizer_is_explicit_and_not_started_on_assembly(self):
  from bili_workbench.native_recognizer import NativeContinuousRecognizer
  from bili_workbench.duplex_adapters import EnergySegmentRecognizer
  self.configure()
  with patch('bili_workbench.native_recognizer._launch_owned') as launch:
   default=build_local_duplex_transport(self.w,**self.args,readiness_probe=lambda:{'ready':True})
   native=build_local_duplex_transport(self.w,**self.args,recognition_backend='windows-continuous',readiness_probe=lambda:{'ready':True})
   try:
    self.assertIsInstance(default.wire.bridge.session.recognizer,EnergySegmentRecognizer);self.assertIsInstance(native.wire.bridge.session.recognizer,NativeContinuousRecognizer);launch.assert_not_called()
   finally:self.assertTrue(default.close());self.assertTrue(native.close())

if __name__=='__main__':unittest.main()
