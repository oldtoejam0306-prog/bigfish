import io,json,tempfile,threading,unittest,urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from bili_workbench.model_stream import StreamControl,StreamCancelled,receive,request,supported
from bili_workbench.runtime import Fault,Vault,HTTP
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP


def chunk(text=None,*,finish=None,usage=None,delta=None):
    item={'id':'fixture-stream','choices':[{'index':0,'delta':delta if delta is not None else ({'content':text} if text is not None else {}),'finish_reason':finish}]}
    if usage is not None:item['usage']=usage
    return ('data: '+json.dumps(item,ensure_ascii=False)+'\n\n').encode('utf8')


def stream(text='模拟回答。',usage=True,done=True):
    raw=chunk(text)+chunk(finish='stop',usage={'prompt_tokens':100,'completion_tokens':20} if usage else None)
    return raw+(b'data: [DONE]\n\n' if done else b'')


class Response(io.BytesIO):
    def __init__(self,raw,mime='text/event-stream'):
        super().__init__(raw);self.headers=Message();self.headers['Content-Type']=mime


class Opener:
    def __init__(self,raw=None):self.raw=stream() if raw is None else raw;self.calls=[];self.before_line=None
    def open(self,req,timeout):
        self.calls.append((req,timeout));outer=self
        class Body(Response):
            def read1(self,n=-1):
                value=super().read1(n)
                if outer.before_line is not None:outer.before_line(value)
                return value
        return Body(self.raw)


class StreamParserTests(unittest.TestCase):
    def test_partial_network_blocks_and_split_utf8_are_reassembled(self):
        class Fragmented(Response):
            def read1(self,n):return super().read1(1)
        result=receive(Fragmented(stream('鲸鱼娘。')),StreamControl());self.assertEqual(result['choices'][0]['message']['content'],'鲸鱼娘。')
    def test_unicode_deltas_require_final_usage_and_done(self):
        raw=b': heartbeat\r\n\r\n'+chunk('鲸鱼')+chunk('娘。')+chunk(finish='stop',usage={'prompt_tokens':2,'completion_tokens':3})+b'data: [DONE]\n\n'
        result=receive(Response(raw),StreamControl());self.assertEqual(result['choices'][0]['message']['content'],'鲸鱼娘。');self.assertEqual(result['usage']['completion_tokens'],3)
    def test_incomplete_usage_finish_and_eof_never_report_success(self):
        for raw in (stream(usage=False),stream(done=False),chunk('x')+b'data: [DONE]\n\n',chunk('x',finish='length',usage={'prompt_tokens':1,'completion_tokens':2})+b'data: [DONE]\n\n'):
            with self.subTest(raw_len=len(raw)),self.assertRaises(Fault):receive(Response(raw),StreamControl())
    def test_tools_reasoning_mixed_ids_and_malformed_frames_are_rejected(self):
        for raw in (chunk(delta={'tool_calls':[{}]}),chunk(delta={'reasoning_content':'hidden'}),chunk('x')+chunk('y').replace(b'fixture-stream',b'different'),b'data: invalid\n\n',b'data: \xff\n\n',b'data: '+b'x'*65537+b'\n\n'):
            with self.subTest(raw_len=len(raw)),self.assertRaises(Fault):receive(Response(raw),StreamControl())
    def test_cancel_before_send_never_opens_transport(self):
        control=StreamControl();control.cancel();opener=Opener()
        with self.assertRaises(StreamCancelled):request(opener,'https://api.deepseek.com/chat/completions',{}, {'model':'deepseek-flash'},control)
        self.assertEqual(opener.calls,[]);self.assertFalse(control.attempted)
    def test_only_known_official_models_are_enabled(self):
        self.assertTrue(supported('https://api.deepseek.com/v1','deepseek-flash'))
        for url,model in (('https://example.test','deepseek-flash'),('https://api.deepseek.com/beta','deepseek-flash'),('https://api.deepseek.com','unknown')):
            self.assertFalse(supported(url,model))
    def test_transport_requests_sse_once_without_fallback(self):
        opener=Opener();control=StreamControl();result=request(opener,'https://api.deepseek.com/chat/completions',{}, {'model':'deepseek-flash'},control)
        payload=json.loads(opener.calls[0][0].data);self.assertEqual(len(opener.calls),1);self.assertTrue(payload['stream']);self.assertEqual(payload['stream_options'],{'include_usage':True});self.assertTrue(control.attempted);self.assertTrue(result['transport_streaming'])
        opener.raw=b'data: [DONE]\n\n'
        with self.assertRaises(Fault):request(opener,'https://api.deepseek.com/chat/completions',{}, {'model':'deepseek-flash'},StreamControl())
        self.assertEqual(len(opener.calls),2)


class GuardedStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.window=patch('bili_workbench.deepseek_test.require_window');self.window.start()
        self.http=FakeHTTP();self.opener=Opener();self.http.opener=self.opener;self.w=Workbench(self.tmp.name,self.http)
        self.w.s.configure({'provider_url':'https://api.deepseek.com','model':'deepseek-flash','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
        self.w.s.vault.save({'api_key':'fixture-secret-not-real'});self.w.s.gate=lambda *args,**kwargs:None
    def tearDown(self):self.w.close();self.window.stop();self.vault.stop();self.tmp.cleanup()
    def send(self,ident='fixture-stream-1',control=None):return self.w.local_chat.send(ident,'你好',stream_control=control or StreamControl())
    def charge(self):return dict(self.w.s.db.execute('SELECT state,actual,reserved FROM charges').fetchone())
    def test_real_guarded_chat_path_settles_usage_and_keeps_private_text_uncached(self):
        result=self.send();self.assertEqual(result['status'],'completed');self.assertEqual(self.charge()['state'],'settled');self.assertEqual(self.charge()['actual'],36)
        payload=json.loads(self.opener.calls[0][0].data);self.assertIn('蓝色小鲸鱼',payload['messages'][0]['content']);self.assertIn('嘴硬心软',payload['messages'][0]['content']);self.assertEqual(self.http.calls,[])
        self.assertEqual(self.w.s.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'model:%'").fetchone()[0],0)
        self.assertEqual(self.send()['status'],'already_recorded');self.assertEqual(len(self.opener.calls),1)
    def test_zero_budget_blocks_before_key_and_transport(self):
        self.w.s.configure({'daily_budget_micro':0})
        with patch.object(self.w.s.vault,'read',side_effect=AssertionError('must not read credentials')):
            with self.assertRaises(Fault):self.send()
        self.assertEqual(self.opener.calls,[]);self.assertEqual(self.w.local_chat.history()['messages'],[])
    def test_cancel_before_start_has_no_charge(self):
        control=StreamControl();control.cancel()
        with self.assertRaises(Fault):self.send(control=control)
        self.assertEqual(self.opener.calls,[]);self.assertIsNone(self.w.s.db.execute('SELECT 1 FROM charges').fetchone())
    def test_cancel_after_send_keeps_uncertain_reservation_and_blocks_new_request(self):
        control=StreamControl();self.opener.before_line=lambda raw:control.cancel()
        with self.assertRaises(Fault):self.send(control=control)
        self.assertEqual(self.charge()['state'],'uncertain');self.assertIsNone(self.charge()['actual']);self.assertEqual(self.charge()['reserved'],10000);self.assertEqual(self.w.local_chat.history()['messages'],[])
        self.assertEqual(self.send()['previous_state'],'uncertain')
        with self.assertRaises(Fault):self.send('fixture-stream-2')
        self.assertEqual(len(self.opener.calls),1)
    def test_missing_usage_and_eof_are_not_settled_or_retried(self):
        self.opener.raw=stream(usage=False)
        with self.assertRaises(Fault):self.send()
        self.assertEqual(self.charge()['state'],'uncertain');self.assertEqual(len(self.opener.calls),1);self.assertEqual(self.w.local_chat.history()['messages'],[])
    def test_secret_spanning_deltas_is_never_returned_or_saved(self):
        self.opener.raw=chunk('fixture-secret-')+chunk('not-real')+chunk(finish='stop',usage={'prompt_tokens':100,'completion_tokens':20})+b'data: [DONE]\n\n'
        with self.assertRaises(Fault):self.send()
        self.assertEqual(self.charge()['state'],'uncertain');self.assertEqual(self.w.local_chat.history()['messages'],[]);self.assertEqual(len(self.opener.calls),1)
    def test_unsupported_endpoint_does_not_fall_back_or_read_key(self):
        self.w.s.configure({'provider_url':'https://example.test/v1'})
        with patch.object(self.w.s.vault,'read',side_effect=AssertionError('must not read credentials')):
            with self.assertRaises(Fault):self.send()
        self.assertEqual(self.opener.calls,[]);self.assertEqual(self.http.calls,[])
    def test_usage_outside_reservation_is_uncertain(self):
        self.opener.raw=chunk('模拟')+chunk(finish='stop',usage={'prompt_tokens':999999999,'completion_tokens':20})+b'data: [DONE]\n\n'
        with self.assertRaises(Fault):self.send()
        self.assertEqual(self.charge()['state'],'uncertain');self.assertEqual(len(self.opener.calls),1)
    def test_off_peak_gate_blocks_stream_before_transport(self):
        from bili_workbench.deepseek_test import OffPeakDeferred
        with patch('bili_workbench.deepseek_test.require_window',side_effect=OffPeakDeferred({})):
            with self.assertRaises(Fault):self.send()
        self.assertEqual(self.opener.calls,[]);self.assertIsNone(self.w.s.db.execute('SELECT 1 FROM charges').fetchone())
    def test_real_loopback_sse_keeps_text_private_until_stream_finished(self):
        first=threading.Event();release=threading.Event();out=[];errors=[];real_opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(chunk('模拟'));self.wfile.flush();first.set();release.wait(3)
                try:self.wfile.write(chunk('回答。')+chunk(finish='stop',usage={'prompt_tokens':100,'completion_tokens':20})+b'data: [DONE]\n\n');self.wfile.flush()
                except (OSError,ConnectionError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        class RoutingOpener:
            def open(self,req,timeout):return real_opener.open(urllib.request.Request('http://127.0.0.1:'+str(server.server_port),data=req.data,headers={'Content-Type':'application/json'},method='POST'),timeout=timeout)
        self.http.opener=RoutingOpener()
        def run():
            try:out.append(self.send())
            except Exception as error:errors.append(type(error).__name__)
        worker=threading.Thread(target=run,daemon=True);worker.start()
        try:
            self.assertTrue(first.wait(3));self.assertTrue(worker.is_alive());self.assertEqual(out,[]);release.set();worker.join(5);self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertEqual(out[0]['reply'],'模拟回答');self.assertEqual(self.charge()['state'],'settled')
        finally:release.set();worker.join(5);server.shutdown();server.server_close();serving.join(3)


if __name__=='__main__':unittest.main()
