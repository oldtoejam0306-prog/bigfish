import io,json,subprocess,tempfile,threading,unittest,wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from bili_workbench.system_voice import synthesize
from bili_workbench.runtime import Fault

class SystemVoiceTests(unittest.TestCase):
    def setUp(self):
        self.ready=patch('bili_workbench.voice_input.readiness',return_value={'ready':True});self.ready.start();self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);exe=self.root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe';exe.parent.mkdir(parents=True);exe.touch();self.calls=[]
    def tearDown(self):self.tmp.cleanup();self.ready.stop()
    def fake_runner(self,args,**kw):
        task=json.loads(kw['input']);self.calls.append((args,kw,task));p=Path(task['output'])
        with wave.open(str(p),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*160)
        return SimpleNamespace(returncode=0)
    def test_explicit_preview_returns_valid_wave_without_shell_interpolation(self):
        text='你好；$(not-a-command)';data=synthesize(text,True,runner=self.fake_runner,platform='nt',system_root=self.root)
        with wave.open(io.BytesIO(data),'rb') as wav:self.assertEqual(wav.getframerate(),16000)
        args,kw,task=self.calls[0];self.assertEqual(task['text'],text);self.assertNotIn(text,' '.join(args));self.assertIn('-File',args);self.assertNotIn('-EncodedCommand',args);self.assertNotIn('shell',kw);self.assertFalse(Path(task['output']).exists())
    def test_guard_blocks_without_launch(self):
        for text,confirmation in [('你好',False),('',True),('x'*301,True),('x\x00y',True)]:
            with self.assertRaises(Fault):synthesize(text,confirmation,runner=self.fake_runner,platform='nt',system_root=self.root)
        self.assertEqual(self.calls,[])
    def test_restricted_policy_blocks_direct_synthesis_before_launch(self):
        with patch('bili_workbench.voice_input.readiness',return_value={'ready':False,'reason':'策略不允许'}):
            with self.assertRaises(Fault):synthesize('你好',True,runner=self.fake_runner,platform='nt',system_root=self.root)
        self.assertEqual(self.calls,[])
    def test_unsupported_platform_is_rejected(self):
        with self.assertRaises(Fault):synthesize('你好',True,runner=self.fake_runner,platform='posix',system_root=self.root)
        self.assertEqual(self.calls,[])
    def test_invalid_wave_is_rejected_and_temporary_file_removed(self):
        outputs=[]
        def bad(args,**kw):
            p=Path(json.loads(kw['input'])['output']);outputs.append(p);p.write_bytes(b'not audio');return SimpleNamespace(returncode=0)
        with self.assertRaises(Fault):synthesize('你好',True,runner=bad,platform='nt',system_root=self.root)
        self.assertFalse(outputs[0].exists())
    def test_cancellation_before_start_never_constructs_child(self):
        with patch('bili_workbench.system_voice.subprocess.Popen') as factory:
            with self.assertRaises(Fault):synthesize('你好',True,platform='nt',system_root=self.root,cancel_check=lambda:True)
            factory.assert_not_called()
    def test_restricted_policy_still_blocks_cancellable_path(self):
        with patch('bili_workbench.voice_input.readiness',return_value={'ready':False}),patch('bili_workbench.system_voice.subprocess.Popen') as factory:
            with self.assertRaises(Fault):synthesize('你好',True,platform='nt',system_root=self.root,cancel_check=lambda:False)
            factory.assert_not_called()
    def test_cancellable_child_sends_text_once_then_collects_wave(self):
        calls=[];outputs=[]
        class Child:
            returncode=None
            def communicate(inner,input=None,timeout=None):
                calls.append(input)
                if len(calls)==1:
                    target=Path(json.loads(input)['output']);outputs.append(target)
                    with wave.open(str(target),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*160)
                    raise subprocess.TimeoutExpired('fixture',.1)
                inner.returncode=0
            def poll(inner):return inner.returncode
            def kill(inner):raise AssertionError('completed child must not be killed')
        factory_calls=[]
        def factory(args,**kwargs):factory_calls.append(args);return Child()
        data=synthesize('模拟合成',True,platform='nt',system_root=self.root,cancel_check=lambda:False,process_factory=factory)
        self.assertTrue(data.startswith(b'RIFF'));self.assertEqual(len(calls),2);self.assertIsNone(calls[1]);self.assertFalse(outputs[0].exists());self.assertIn('-File',factory_calls[0]);self.assertNotIn('-ExecutionPolicy',factory_calls[0])
    def test_cancel_during_synthesis_waits_for_owned_child_exit_and_returns_no_audio(self):
        cancelled=threading.Event();calls=[];child=None
        class Child:
            returncode=None;kills=0
            def communicate(inner,input=None,timeout=None):
                calls.append(input)
                if timeout is not None:cancelled.set();raise subprocess.TimeoutExpired('fixture',.1)
                inner.returncode=-9
            def poll(inner):return inner.returncode
            def kill(inner):inner.kills+=1
        child=Child()
        with self.assertRaises(Fault):synthesize('模拟合成',True,platform='nt',system_root=self.root,cancel_check=cancelled.is_set,process_factory=lambda *args,**kwargs:child)
        self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9);self.assertEqual(len(calls),2);self.assertIsNone(calls[1])

if __name__=='__main__':unittest.main()
