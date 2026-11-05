import json,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical,Fault
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP,BV

class MultiPartSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start()
        self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.w.s.gate=lambda *a,**k:None
        self.w.s.configure({'read_enabled':True,'account_mid':'123'})
        self.info=patch.object(self.w.bili,'info',return_value={'bvid':BV,'aid':100,'cid':200,'title':'多P视频','pages':[{'cid':200,'part':'第一集'},{'cid':201,'part':'第二集'}]});self.info.start()
        self.identity=patch.object(self.w.bili,'ensure_read_identity');self.identity.start()
        self.player=patch.object(self.w.bili,'get',side_effect=lambda path,params,*a,**k:{'subtitle':{'subtitles':[{'lan':'zh','subtitle_url':'https://aisubtitle.hdslb.com/'+str(params['cid'])+'.json'}]}});self.player_mock=self.player.start()
        self.http.json=lambda method,url,*a,**k:{'body':[{'from':0,'to':1,'content':'第'+('二' if '201.json' in url else '一')+'集字幕'}]}
    def tearDown(self):
        self.w.close();self.player.stop();self.identity.stop();self.info.stop();self.vault.stop();self.tmp.cleanup()
    def test_parts_are_cached_and_recorded_separately(self):
        first=self.w.read_video_subtitle(BV);second=self.w.read_video_subtitle(BV,part_index=2);again=self.w.read_video_subtitle(BV,part_index=2)
        self.assertEqual((first['cid'],first['part_index'],first['text']),(200,1,'第一集字幕'))
        self.assertEqual((second['cid'],second['part_index'],second['parts_total'],second['text']),(201,2,2,'第二集字幕'))
        self.assertTrue(again['cached']);self.assertEqual(self.player_mock.call_count,2)
        self.assertNotEqual(first['content_revision'],second['content_revision'])
        self.assertEqual(self.w.s.db.execute("SELECT count(*) FROM materials WHERE id LIKE 'bili:%'").fetchone()[0],2)
        self.assertTrue(any('第2P' in item['label'] for item in self.w.character_overview()['recent_activity']))
        self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_invalid_part_stops_before_player_request(self):
        for part in (0,3,True,'2'):
            with self.assertRaises(Fault):self.w.read_video_subtitle(BV,part_index=part)
        self.player_mock.assert_not_called()
    def test_analysis_uses_selected_part_and_preserves_identity(self):
        from unittest.mock import patch
        with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}), patch.object(self.w.model,'plan',return_value={'ready':True}), patch.object(self.w.model,'summarize',return_value={'model':'fixture','text':'模拟分析','coverage':'仅字幕','cached':False}) as summarize:
            first=self.w._confirmed_analyze(BV,'request-one');second=self.w._confirmed_analyze(BV,'request-two',part_index=2)
        self.assertNotEqual(first['material_id'],second['material_id'])
        payload=json.loads(summarize.call_args_list[1].args[0]);self.assertEqual((payload['cid'],payload['part_index'],payload['untrusted_subtitle']),(201,2,'第二集字幕'))
        row=self.w.s.db.execute('SELECT content FROM materials WHERE id=?',(second['material_id'],)).fetchone()
        self.assertEqual(json.loads(row[0])['input_source']['part_index'],2)
        self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_media_uses_selected_part_and_rejects_changed_cid(self):
        calls=[]
        def get(path,params,*args,**kwargs):
            calls.append(params['cid']);return {'durl':[{'size':4,'url':'https://fixture.bilivideo.com/video.mp4'}]}
        with patch.object(self.w.bili,'get',side_effect=get), patch.object(self.http,'request',return_value=(b'test',{}),create=True):
            self.assertEqual(self.w.bili.media(BV,part_index=2,expected_cid=201),b'test')
            with self.assertRaises(Fault):self.w.bili.media(BV,part_index=2,expected_cid=200)
        self.assertEqual(calls,[201])
    def test_local_api_selects_part_without_model(self):
        server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        request=urllib.request.Request(base+'/api/subtitle',data=canonical({'bvid':BV,'part_index':2}).encode(),headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base},method='POST')
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:result=json.load(response)
            self.assertEqual((result['part_index'],result['text']),(2,'第二集字幕'))
            self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
