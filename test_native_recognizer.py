import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bili_workbench.native_recognizer import NativeContinuousRecognizer,RecognitionProtocol
from bili_workbench.duplex_session import SpeechStarted,FinalTranscript
from bili_workbench.runtime import Fault


def wire(seq,kind,**fields):return (json.dumps(dict(seq=seq,type=kind,**fields),ensure_ascii=False)+'\n').encode('utf8')
def ready():return wire(1,'ready',version=1,rate=16000,channels=1,sample_bytes=2)


class PipeChild:
    def __init__(self,on_frame=None,*,defer_kill=False):
        self.stdin=self;self.stdout=self;self.lines=asyncio.Queue();self.lines.put_nowait(ready());self.sent=[];self.seq=1;self.on_frame=on_frame;self.returncode=None;self.kills=0;self.defer_kill=defer_kill;self.exited=asyncio.Event();self.closed=False;self.reading=asyncio.Event()
    def emit(self,kind,**fields):self.seq+=1;self.lines.put_nowait(wire(self.seq,kind,**fields))
    def write(self,frame):
        self.sent.append(frame)
        if self.on_frame is not None:self.on_frame(self,frame)
    async def drain(self):await asyncio.sleep(0)
    def close(self):
        if self.closed:return
        self.closed=True
        if not self.kills:
            self.emit('completed',ok=True);self.lines.put_nowait(b'');self.returncode=0;self.exited.set()
    async def readline(self):self.reading.set();return await self.lines.get()
    async def read(self,n):return await self.lines.get()
    def kill(self):
        self.kills+=1;self.lines.put_nowait(b'')
        if not self.defer_kill:self.returncode=-9;self.exited.set()
    async def wait(self):
        await self.exited.wait()
        if self.returncode is None:self.returncode=-9 if self.kills else 0
        return self.returncode


class ProtocolTests(unittest.TestCase):
    def test_two_final_results_and_rejection_keep_distinct_utterances(self):
        p=RecognitionProtocol();self.assertIsNone(p.accept(ready()));self.assertIsInstance(p.accept(wire(2,'speech',utterance=1)),SpeechStarted)
        self.assertEqual(p.accept(wire(3,'final',utterance=1,text='第一句')).text,'第一句');p.accept(wire(4,'speech',utterance=2));self.assertIsNone(p.accept(wire(5,'rejected',utterance=2)))
        p.accept(wire(6,'speech',utterance=3));self.assertEqual(p.accept(wire(7,'final',utterance=3,text='第三句')).text,'第三句');p.accept(wire(8,'completed',ok=True));self.assertTrue(p.completed);self.assertEqual((p.finals,p.rejected),(2,1))
    def test_duplicate_gap_bool_sequence_and_bad_handshake_are_rejected(self):
        for raw in (wire(1,'ready',version=True,rate=16000,channels=1,sample_bytes=2),wire(True,'ready'),wire(2,'ready'),wire(1,'ready',version=1,rate=44100,channels=1,sample_bytes=2)):
            with self.assertRaises(Fault):RecognitionProtocol().accept(raw)
        for seq in (1,3):
            p=RecognitionProtocol();p.accept(ready())
            with self.assertRaises(Fault):p.accept(wire(seq,'speech',utterance=1))
    def test_late_cross_utterance_partial_and_unfinished_events_never_become_text(self):
        for kind,fields in (('final',{'utterance':2,'text':'错配'}),('partial',{'utterance':1,'text':'假设'}),('completed',{'ok':True}),('speech',{'utterance':2})):
            p=RecognitionProtocol();p.accept(ready());p.accept(wire(2,'speech',utterance=1))
            with self.assertRaises(Fault):p.accept(wire(3,kind,**fields))
    def test_incomplete_oversize_or_error_events_are_fail_closed(self):
        for raw in (b'{"seq":1}',b'x'*8193+b'\n',wire(1,'error',code='host-unavailable')):
            with self.assertRaises(Fault):RecognitionProtocol().accept(raw)


class NativeRecognizerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);exe=self.root/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe';exe.parent.mkdir(parents=True);exe.touch();self.calls=[]
        self.ready=patch('bili_workbench.voice_input.readiness',return_value={'ready':True});self.ready.start()
    async def asyncTearDown(self):self.ready.stop();self.tmp.cleanup()
    def recognizer(self,child):
        async def factory(*args,**kwargs):self.calls.append((args,kwargs));return child
        return NativeContinuousRecognizer(confirmation=True,process_factory=factory,platform='nt',system_root=self.root)
    async def test_multiple_utterances_are_emitted_before_input_eof_on_one_host(self):
        release=asyncio.Event()
        def recognize(child,frame):
            utterance=len(child.sent);child.emit('speech',utterance=utterance);child.emit('final',utterance=utterance,text='模拟'+str(utterance))
        child=PipeChild(recognize);r=self.recognizer(child)
        async def frames():yield b'\x01\x00';await release.wait();yield b'\x02\x00'
        source=r.events(frames());first=await asyncio.wait_for(anext(source),2);second=await asyncio.wait_for(anext(source),2)
        self.assertIsInstance(first,SpeechStarted);self.assertEqual(second.text,'模拟1');self.assertFalse(child.closed);self.assertIsNone(child.returncode)
        release.set();rest=[item async for item in source];self.assertEqual([type(x) for x in rest],[SpeechStarted,FinalTranscript]);self.assertEqual(rest[-1].text,'模拟2');self.assertEqual(child.sent,[b'\x01\x00',b'\x02\x00']);self.assertEqual(child.kills,0);self.assertEqual(len(self.calls),1);self.assertTrue(r.protocol.completed)
        args,kwargs=self.calls[0];self.assertIn('-File',args);self.assertNotIn('-ExecutionPolicy',args);self.assertNotIn('-EncodedCommand',args);self.assertNotIn('shell',kwargs)
    async def test_policy_and_confirmation_deny_before_child_creation(self):
        async def frames():yield b'\0\0'
        with patch('bili_workbench.voice_input.readiness',return_value={'ready':False}):
            with self.assertRaises(Fault):[item async for item in self.recognizer(PipeChild()).events(frames())]
        with self.assertRaises(Fault):[item async for item in NativeContinuousRecognizer().events(frames())]
        self.assertEqual(self.calls,[])
    async def test_protocol_gap_kills_only_owned_host_without_replay(self):
        child=PipeChild(lambda child,frame:child.lines.put_nowait(wire(4,'speech',utterance=1)))
        async def frames():yield b'\0\0';await asyncio.Event().wait()
        with self.assertRaises(Fault):[item async for item in self.recognizer(child).events(frames())]
        self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9);self.assertEqual(len(self.calls),1)
    async def test_invalid_pcm_writer_failure_is_not_hidden_by_reader(self):
        child=PipeChild()
        async def frames():yield b'odd'
        with self.assertRaises(Fault):[item async for item in self.recognizer(child).events(frames())]
        self.assertEqual(child.sent,[]);self.assertEqual(len(self.calls),1)
    async def test_early_eof_and_missing_completion_are_errors(self):
        child=PipeChild(lambda child,frame:child.lines.put_nowait(b''))
        async def frames():yield b'\0\0';await asyncio.Event().wait()
        with self.assertRaises(Fault):[item async for item in self.recognizer(child).events(frames())]
        self.assertEqual(child.kills,1)
    async def test_completion_while_input_is_open_is_not_success(self):
        child=PipeChild(lambda child,frame:child.emit('completed',ok=True))
        async def frames():yield b'\0\0';await asyncio.Event().wait()
        with self.assertRaises(Fault):[item async for item in self.recognizer(child).events(frames())]
        self.assertEqual(child.kills,1);self.assertEqual(len(self.calls),1)
    async def test_cancel_read_keeps_task_pending_until_exit_ack(self):
        child=PipeChild(defer_kill=True);r=self.recognizer(child)
        async def frames():await asyncio.Event().wait();yield b'\0\0'
        task=asyncio.create_task(anext(r.events(frames())))
        try:
            while not self.calls:await asyncio.sleep(.001)
            await asyncio.sleep(.02);task.cancel()
            while not child.kills:await asyncio.sleep(.001)
            self.assertFalse(task.done());task.cancel();await asyncio.sleep(.01);self.assertFalse(task.done());child.exited.set()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9)
        finally:child.exited.set()
    async def test_cancel_creation_stops_late_child_before_returning(self):
        child=PipeChild();started=asyncio.Event();release=asyncio.Event()
        async def factory(*args,**kwargs):started.set();await release.wait();return child
        r=NativeContinuousRecognizer(confirmation=True,process_factory=factory,platform='nt',system_root=self.root)
        async def frames():yield b'\0\0'
        task=asyncio.create_task(anext(r.events(frames())));await started.wait();task.cancel();await asyncio.sleep(.01);self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(child.kills,1);self.assertEqual(child.returncode,-9)


if __name__=='__main__':unittest.main()
