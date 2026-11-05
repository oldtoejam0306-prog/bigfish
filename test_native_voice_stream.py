import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bili_workbench.native_voice_stream import stream_pcm
from bili_workbench.duplex_adapters import WindowsPCMStreamSynthesizer
from bili_workbench.runtime import Fault


class Input:
    def __init__(self):self.data=[];self.closed=False
    def write(self,data):self.data.append(data)
    async def drain(self):pass
    def close(self):self.closed=True


class Child:
    def __init__(self,blocks):
        self.stdin=Input();self.stdout=self;self.blocks=list(blocks);self.returncode=None;self.kills=0;self.allow_exit=asyncio.Event();self.reading=asyncio.Event()
    async def read(self,n):
        self.reading.set()
        if self.blocks:return self.blocks.pop(0)
        await self.allow_exit.wait();return b''
    def kill(self):self.kills+=1;self.returncode=-9;self.allow_exit.set()
    async def wait(self):await self.allow_exit.wait();self.returncode=0 if self.returncode is None else self.returncode;return self.returncode


class NativeVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);exe=self.root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe';exe.parent.mkdir(parents=True);exe.touch()
        self.ready=patch('bili_workbench.voice_input.readiness',return_value={'ready':True});self.ready.start();self.calls=[]
    async def asyncTearDown(self):self.ready.stop();self.tmp.cleanup()
    def output(self,child,**kwargs):
        async def factory(*args,**options):self.calls.append((args,options));return child
        return stream_pcm('模拟完整回复。',True,process_factory=factory,platform='nt',system_root=self.root,**kwargs)
    async def test_pipe_yields_pcm_before_child_exit_and_close_waits_for_owned_process(self):
        child=Child([b'\x01\x00'*1600]);source=self.output(child)
        first=await asyncio.wait_for(anext(source),2);self.assertEqual(len(first),3200);self.assertIsNone(child.returncode)
        self.assertEqual(json.loads(child.stdin.data[0])['text'],'模拟完整回复。');self.assertTrue(child.stdin.closed);self.assertEqual(len(child.stdin.data),1)
        await source.aclose();self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9)
        args,options=self.calls[0];self.assertIn('-File',args);self.assertNotIn('-ExecutionPolicy',args);self.assertNotIn('-EncodedCommand',args);self.assertNotIn('shell',options)
    async def test_partial_pcm_blocks_are_reassembled_and_normal_exit_not_killed(self):
        child=Child([b'\x01',b'\x00'*3199,b'\x02\x00']);child.allow_exit.set()
        result=[item async for item in self.output(child)];self.assertEqual([len(x) for x in result],[3200,2]);self.assertEqual(child.returncode,0);self.assertEqual(child.kills,0)
    async def test_failed_exit_odd_audio_and_container_are_rejected_without_retry(self):
        for blocks,code in (([b'x'],0),([b'\x01\x00'],7),([b'RI',b'FF'+b'\0'*3196],0)):
            child=Child(blocks);child.returncode=code;child.allow_exit.set()
            with self.assertRaises(Fault):[item async for item in self.output(child)]
        self.assertEqual(len(self.calls),3)
    async def test_policy_denial_and_no_confirmation_never_construct_child(self):
        child=Child([])
        with patch('bili_workbench.voice_input.readiness',return_value={'ready':False}):
            with self.assertRaises(Fault):await anext(self.output(child))
        with self.assertRaises(Fault):await anext(stream_pcm('模拟',False))
        self.assertEqual(self.calls,[])
    async def test_cancel_during_read_waits_for_child_exit(self):
        child=Child([]);task=asyncio.create_task(anext(self.output(child)));await child.reading.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9)
    async def test_cancel_during_creation_still_owns_and_stops_late_child(self):
        child=Child([]);started=asyncio.Event();release=asyncio.Event()
        async def factory(*args,**kwargs):started.set();await release.wait();return child
        source=stream_pcm('模拟',True,process_factory=factory,platform='nt',system_root=self.root);task=asyncio.create_task(anext(source));await started.wait();task.cancel();await asyncio.sleep(.02);self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9)
    async def test_adapter_fully_validates_before_native_stream_is_started(self):
        calls=[]
        async def fake(text,confirmation):calls.append(text);yield b'\x01\x00'
        async def invalid():yield '你好。';yield '我是';yield '真实的人类。'
        with self.assertRaises(Fault):[item async for item in WindowsPCMStreamSynthesizer(fake).stream_pcm(invalid())]
        self.assertEqual(calls,[])
        async def valid():yield '你好。';yield '模拟回复。'
        frames=[item async for item in WindowsPCMStreamSynthesizer(fake).stream_pcm(valid())];self.assertEqual(frames,[b'\x01\x00']);self.assertEqual(calls,['你好。模拟回复。'])


if __name__=='__main__':unittest.main()
