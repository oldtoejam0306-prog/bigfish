import hashlib,hmac,json,tempfile,threading,time,unittest
import urllib.request,urllib.error
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from bili_workbench.runtime import canonical
from test_workbench import FakeHTTP
class NativeWebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
        self.w.s.configure({'qq_endpoint':'http://127.0.0.1:9999'})
        self.w.qq.configure({'bot_id':10001,'owner_id':20002,'enabled':True})
        self.w.s.vault.save({'qq_access_token':'fixture-access-token-0000','qq_event_secret':'fixture-event-secret-0000'})
        self.server=Server(('127.0.0.1',0),self.w);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.w.close();self.tmp.cleanup()
    def body(self):return canonical({'time':int(time.time()),'self_id':10001,'user_id':20002,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'message':[{'type':'text','data':{'text':'真实协议fixture，不是实际QQ'}}]}).encode()
    def test_local_video_preview_is_authenticated_mp4_and_vtt(self):
        import shutil
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):self.skipTest('ffmpeg not installed')
        plan=self.w.creation.plan({'title':'本地预览','public_use_confirmed':True,'scenes':[{'text':'AI原创<不是HTML>','duration':1,'color':'#123456','source':'合成fixture'}]})
        self.w.creation.render(plan['id'],'确认制作公开素材')
        headers={'Content-Type':'application/json','X-Local-Token':self.server.token,'Origin':self.url}
        for endpoint,kind in (('media','video/mp4'),('captions','text/vtt')):
            request=urllib.request.Request(self.url+'/api/creation/'+endpoint,data=canonical({'id':plan['id']}).encode(),headers=headers,method='POST')
            with self.opener.open(request,timeout=10) as response:
                raw=response.read();self.assertIn(kind,response.headers['Content-Type'])
                if endpoint=='media':self.assertEqual(raw[4:8],b'ftyp')
                else:self.assertTrue(raw.startswith(b'WEBVTT'));self.assertIn(b'&lt;',raw)
        self.assertEqual(self.http.calls,[])
    def test_signed_event_ack_is_empty_204_not_quick_reply_or_model_call(self):
        body=self.body();headers={'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(b'fixture-event-secret-0000',body,hashlib.sha1).hexdigest()}
        request=urllib.request.Request(self.url+'/onebot/event',data=body,headers=headers,method='POST')
        with self.opener.open(request,timeout=5) as r:self.assertEqual(r.status,204);self.assertEqual(r.read(),b'')
        self.assertEqual(self.w.qq_inbox_status()['inbox'][0]['state'],'pending');self.assertEqual(self.http.calls,[])
        self.assertEqual(self.w.process_qq_inbox()['status'],'needs_transport_validation');self.assertEqual(self.http.calls,[])
    def test_unsigned_event_rejected_and_credentials_save_never_echoes(self):
        request=urllib.request.Request(self.url+'/onebot/event',data=self.body(),headers={'Content-Type':'application/json'},method='POST')
        with self.assertRaises(urllib.error.HTTPError) as e:self.opener.open(request,timeout=5)
        self.assertEqual(e.exception.code,403);self.assertEqual(self.w.qq_inbox_status()['inbox'],[])
        headers={'Content-Type':'application/json','X-Local-Token':self.server.token,'Origin':self.url}
        request=urllib.request.Request(self.url+'/api/config',data=canonical({'config':{},'api_key':'fixture-secret'}).encode(),headers=headers,method='POST')
        with self.opener.open(request,timeout=5) as r:response=r.read().decode();self.assertNotIn('fixture-secret',response)
        request=urllib.request.Request(self.url+'/api/status',headers={'X-Local-Token':self.server.token})
        with self.opener.open(request,timeout=5) as r:response=r.read().decode();self.assertNotIn('fixture-secret',response)
    def test_duplicate_signature_header_is_rejected_before_inbox(self):
        import http.client
        body=self.body();signature='sha1='+hmac.new(b'fixture-event-secret-0000',body,hashlib.sha1).hexdigest()
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        try:
            conn.putrequest('POST','/onebot/event');conn.putheader('Content-Type','application/json');conn.putheader('Content-Length',str(len(body)))
            conn.putheader('X-Self-ID','10001');conn.putheader('X-Signature',signature);conn.putheader('x-signature',signature)
            conn.endheaders(body);response=conn.getresponse();self.assertEqual(response.status,403);response.read()
            self.assertEqual(self.w.qq_inbox_status()['inbox'],[]);self.assertEqual(self.http.calls,[])
        finally:conn.close()

if __name__=='__main__':unittest.main()
