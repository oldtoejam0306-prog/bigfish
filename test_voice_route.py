import http.client,json,socket,tempfile,threading,time,unittest,urllib.request,urllib.error
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class VoiceRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.w=Workbench(self.tmp.name,FakeHTTP());self.servers=[]
    def tearDown(self):
        for server,thread in self.servers:server.shutdown();server.server_close();thread.join()
        self.w.close();self.vault.stop();self.tmp.cleanup()
    def open(self,preview,auth=True):
        server=Server(('127.0.0.1',0),self.w,preview=preview);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread));base='http://127.0.0.1:'+str(server.server_port)
        headers={'Content-Type':'application/json','Origin':base};
        if auth:headers['X-Local-Token']=server.token
        req=urllib.request.Request(base+'/api/voice/preview',data=canonical({'text':'测试','confirmation':True}).encode(),headers=headers,method='POST')
        try:return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5)
        except urllib.error.HTTPError as error:
            error.read();error.close();raise
    def test_authorized_response_and_readonly_preview_denial(self):
        with patch('bili_workbench.system_voice.synthesize',return_value=b'fake-wav') as synth:
            with self.open(False) as r:self.assertEqual((r.headers['Content-Type'],r.read()),('audio/wav',b'fake-wav'))
            with self.assertRaises(urllib.error.HTTPError) as e:self.open(True)
            self.assertEqual(e.exception.code,403);self.assertEqual(synth.call_count,1)
    def test_unauthorized_does_not_invoke_synthesis(self):
        with patch('bili_workbench.system_voice.synthesize') as synth:
            with self.assertRaises(urllib.error.HTTPError) as e:self.open(False,auth=False)
            self.assertEqual(e.exception.code,403);synth.assert_not_called()

    def raw_denied_request(self,partial=False):
        server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread))
        body=canonical({'text':'fixture only','confirmation':True}).encode()
        size=len(body)+100 if partial else len(body)
        header=('POST /api/voice/preview HTTP/1.0\r\nHost: 127.0.0.1:'+str(server.server_port)+'\r\nContent-Type: application/json\r\nContent-Length: '+str(size)+'\r\n\r\n').encode()
        with socket.create_connection(('127.0.0.1',server.server_port),timeout=2) as sock:
            sock.sendall(header+body[:4])
            if not partial:
                time.sleep(.02);sock.sendall(body[4:])
            started=time.monotonic();response=http.client.HTTPResponse(sock);response.begin();raw=response.read()
            return response.status,json.loads(raw),time.monotonic()-started
    def test_denied_split_body_returns_json_without_synthesis_or_reset(self):
        with patch('bili_workbench.system_voice.synthesize') as synth:
            status,result,_=self.raw_denied_request()
            self.assertEqual(status,403);self.assertEqual(result['error'],'本机页面会话无效');synth.assert_not_called()
    def test_incomplete_denied_body_has_a_bounded_drain(self):
        with patch('bili_workbench.system_voice.synthesize') as synth:
            status,result,elapsed=self.raw_denied_request(partial=True)
            self.assertEqual(status,403);self.assertLess(elapsed,1.5);synth.assert_not_called()

if __name__=='__main__':unittest.main()
