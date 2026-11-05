import base64,io,json,subprocess,unittest,wave
from unittest.mock import patch
from bili_workbench.runtime import Fault
from bili_workbench.voice_input import transcribe_wav,readiness

def fixture_wav(rate=16000,seconds=1):
 out=io.BytesIO()
 with wave.open(out,'wb') as audio:
  audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(rate);audio.writeframes(b'\x00\x00'*(rate*seconds))
 return base64.b64encode(out.getvalue()).decode()

class VoiceInputTests(unittest.TestCase):
 def test_invalid_input_never_spawns_a_process(self):
  with patch('bili_workbench.voice_input.subprocess.run') as run:
   for value in ('invalid',fixture_wav(rate=8000),fixture_wav(seconds=11)):
    with self.assertRaises(Fault):transcribe_wav(value)
   run.assert_not_called()
 def test_valid_fixture_goes_to_stdin_and_never_to_model(self):
  response=subprocess.CompletedProcess([],0,json.dumps({'text':'测试','recognized':True},ensure_ascii=False).encode('utf8'),b'')
  with patch('bili_workbench.voice_input.readiness',return_value={'ready':True}), patch('bili_workbench.voice_input.subprocess.run',return_value=response) as run:
   result=transcribe_wav(fixture_wav())
   self.assertEqual(result['text'],'测试');self.assertFalse(result['model_called']);self.assertFalse(result['audio_persisted'])
   self.assertTrue(run.call_args.kwargs['input'].startswith(b'RIFF'));self.assertNotIn('测试',str(run.call_args.args[0]))
 def test_script_denial_is_reported_without_error_contents(self):
  denied=subprocess.CompletedProcess([],1,b'',b'sensitive diagnostic text')
  with patch('bili_workbench.voice_input.readiness',return_value={'ready':True}), patch('bili_workbench.voice_input.subprocess.run',return_value=denied):
   with self.assertRaises(Fault) as error:transcribe_wav(fixture_wav())
   self.assertIn('执行策略',str(error.exception));self.assertNotIn('sensitive',str(error.exception))
 def test_direct_transcription_stops_before_script_when_policy_disallows(self):
  with patch('bili_workbench.voice_input.readiness',return_value={'ready':False,'reason':'策略不允许'}), patch('bili_workbench.voice_input.subprocess.run') as run:
   with self.assertRaises(Fault):transcribe_wav(fixture_wav())
   run.assert_not_called()
 def test_readiness_detects_restricted_policy_without_running_script(self):
  result=subprocess.CompletedProcess([],0,b'Restricted\r\n',b'')
  with patch('bili_workbench.voice_input.subprocess.run',return_value=result) as run:
   status=readiness();self.assertFalse(status['ready'])
   self.assertEqual(run.call_args.args[0][-1],'Get-ExecutionPolicy')

if __name__=='__main__':unittest.main()
