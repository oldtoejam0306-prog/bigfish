import json,tempfile,threading,unittest,urllib.request,urllib.error
from bili_workbench.runtime import canonical
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_bili_readiness import ReadHTTP
from test_workbench import BV

class ManualVideoActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=ReadHTTP();self.w=Workbench(self.tmp.name,self.http)
        self.w.s.configure({'read_enabled':True,'account_mid':'123'});self.w.s.gate=lambda *a,**k:None
        self.server=Server(('127.0.0.1',0),self.w);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port);self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.w.close();self.tmp.cleanup()
    def post(self,path,data):
        req=urllib.request.Request(self.base+'/api/'+path,data=canonical(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':self.server.token,'Origin':self.base},method='POST')
        with self.opener.open(req,timeout=5) as response:return json.load(response)
    def overview(self):
        req=urllib.request.Request(self.base+'/api/character-overview',headers={'X-Local-Token':self.server.token})
        with self.opener.open(req,timeout=5) as response:return json.load(response)
    def test_manual_info_and_subtitle_appear_without_model_or_outbound(self):
        self.post('info',{'bvid':BV});self.post('subtitle',{'bvid':BV})
        activity=self.overview()['recent_activity'];labels={r['label'] for r in activity}
        self.assertIn('手动查看视频信息',labels);self.assertIn('手动取得字幕',labels)
        self.assertTrue(all(r['reference']==BV for r in activity));self.assertFalse(any('/chat/completions' in c[1] for c in self.http.calls))
        self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM public_video_observations').fetchone()[0],0)
        self.post('info',{'bvid':BV});self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM manual_video_reads').fetchone()[0],2)
        self.w.s.configure({'account_mid':'456'});self.assertEqual(self.overview()['recent_activity'],[])
    def test_absent_subtitle_and_failed_read_are_not_claimed_as_understood(self):
        self.http.no_sub=True;self.post('subtitle',{'bvid':BV})
        self.assertEqual(self.overview()['recent_activity'][0]['label'],'手动查找字幕：未提供')
        count=self.w.s.db.execute('SELECT count(*) FROM manual_video_reads').fetchone()[0]
        with self.assertRaises(urllib.error.HTTPError):self.post('info',{'bvid':'not-a-bvid'})
        self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM manual_video_reads').fetchone()[0],count)

if __name__=='__main__':unittest.main()
