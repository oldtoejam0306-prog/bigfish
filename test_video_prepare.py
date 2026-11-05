import json,tempfile,threading,unittest,urllib.request
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP
from bili_workbench.web import Server

BV='BV1eRJS67EJP'
def subtitle(text='测试字幕'):
 return {'bvid':BV,'cid':123,'part_index':1,'parts_total':1,'title':'测试视频','text':text,'content_revision':'fixture'}

class VideoPreparationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY','max_input_tokens':2048,'max_output_tokens':128})
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_plan_counts_calls_and_requires_explicit_confirmation(self):
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',return_value=subtitle('测'*1200)),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no secrets')) as vault,patch.object(self.w,'_confirmed_analyze',return_value={'result':'fixture','model_called':True}) as analyze:
   plan=self.w.video_preparation.prepare(BV,'subtitle',1)
   self.assertTrue(plan['ready']);self.assertGreater(plan['max_model_calls'],2);self.assertFalse(plan['credentials_checked']);vault.assert_not_called();analyze.assert_not_called()
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'')
   result=self.w.video_preparation.execute(plan['id'],'确认最多调用'+str(plan['max_model_calls'])+'次模型')
   self.assertFalse(result['automatic_retry']);self.assertEqual(analyze.call_count,1)
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'确认最多调用'+str(plan['max_model_calls'])+'次模型')
  self.assertEqual(self.http.calls,[])
 def test_changes_to_subtitle_or_budget_block_before_model(self):
  values=[subtitle(),subtitle('变化字幕')]
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',side_effect=values),patch.object(self.w,'_confirmed_analyze') as analyze:
   plan=self.w.video_preparation.prepare(BV,'subtitle',1)
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
   analyze.assert_not_called()
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w,'_confirmed_analyze') as analyze:
   plan=self.w.video_preparation.prepare(BV,'subtitle',1);self.w.s.configure({'daily_budget_micro':90000})
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
   analyze.assert_not_called()
 def test_no_subtitles_or_zero_budget_never_calls_model(self):
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',return_value=subtitle('')),patch.object(self.w,'_confirmed_analyze') as analyze:
   plan=self.w.video_preparation.prepare(BV,'subtitle',1);self.assertFalse(plan['ready']);self.assertEqual(plan['coverage'],'无字幕');analyze.assert_not_called()
  self.w.s.configure({'paid_enabled':False})
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no secrets')):
   self.assertFalse(self.w.video_preparation.prepare(BV,'subtitle',1)['ready'])
  self.assertEqual(self.http.calls,[])
 def test_fixture_subtitle_to_confirmed_model_end_to_end(self):
  from test_workbench import BV as fixture_bv
  self.w.s.configure({'read_enabled':True,'account_mid':'123','max_input_tokens':16000,'max_output_tokens':1000})
  self.w.s.vault.save({'api_key':'fake-test-key','cookie':'SESSDATA=fixture; bili_jct=fixture','csrf':'fixture'});self.w.s.gate=lambda *args,**kwargs:None
  plan=self.w.video_preparation.prepare(fixture_bv,'subtitle',1)
  self.assertTrue(plan['ready']);self.assertTrue(plan['subtitle_available'])
  self.assertEqual(len([x for x in self.http.calls if x[1].endswith('/chat/completions')]),0)
  result=self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
  self.assertTrue(result['result']['model_called'])
  self.assertEqual(len([x for x in self.http.calls if x[1].endswith('/chat/completions')]),1)
  self.assertEqual(result['result']['result']['coverage'],'仅字幕')
 def test_unknown_analysis_result_never_replays_same_plan(self):
  with patch.object(self.w.qq,'config',return_value={'acceptance_owner_only':False}),patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w,'_confirmed_analyze',side_effect=Fault('fixture failure')) as analyze:
   plan=self.w.video_preparation.prepare(BV,'subtitle',1);confirm='确认最多调用1次模型'
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],confirm)
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],confirm)
   self.assertEqual(analyze.call_count,1)
 def test_legacy_analysis_is_blocked_before_network_and_secrets(self):
  with patch.object(self.w.bili,'subtitle') as subtitle_read,patch.object(self.w.model,'chat') as chat,patch.object(self.w.model,'summarize') as summarize,patch.object(self.w.s.vault,'read') as vault:
   result=self.w.analyze(BV,'legacy-request')
   self.assertEqual(result['status'],'needs_video_confirmation');self.assertFalse(result['model_called'])
   subtitle_read.assert_not_called();chat.assert_not_called();summarize.assert_not_called();vault.assert_not_called()
  self.assertEqual(self.http.calls,[])
 def test_legacy_browse_cannot_start_paid_analysis(self):
  with patch.object(self.w.bili,'feed',return_value={'list':[{'bvid':BV,'title':'fixture'}]}),patch.object(self.w,'_confirmed_analyze') as analyze:
   result=self.w.browse('legacy-browse')
   self.assertEqual(result['results'][0]['analysis']['status'],'needs_video_confirmation')
   analyze.assert_not_called()
 def test_legacy_http_endpoint_is_blocked_without_subtitle_or_model(self):
  server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   base='http://127.0.0.1:'+str(server.server_port)
   body=json.dumps({'bvid':BV,'request_id':'legacy-test','mode':'subtitle'}).encode()
   req=urllib.request.Request(base+'/api/analyze',data=body,headers={'Content-Type':'application/json','X-Local-Token':server.token})
   with patch.object(self.w.bili,'subtitle') as subtitle_read,patch.object(self.w,'_confirmed_analyze') as analyze:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:result=json.load(response)
    self.assertEqual(result['status'],'needs_video_confirmation');self.assertFalse(result['model_called'])
    subtitle_read.assert_not_called();analyze.assert_not_called()
   self.assertEqual(self.http.calls,[])
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
