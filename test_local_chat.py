import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from test_workbench import FakeHTTP

class LocalChatTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def enable_budget(self):self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
 def test_zero_budget_never_calls_model_or_records_message(self):
  with patch.object(self.w.model,'chat') as model:
   with self.assertRaises(Fault):self.w.local_chat.send('fixture-01','你好')
   model.assert_not_called();self.assertEqual(self.w.local_chat.history()['messages'],[]);self.assertEqual(self.http.calls,[])
 def test_owner_history_and_explicit_memory_selection(self):
  self.enable_budget();self.w.growth.controls().remember(Scope.owner(),'喜欢鲸鱼','fixture:本人明确提供',0.8)
  with patch.object(self.w.model,'chat',return_value={'text':'你好，我在。','cached':False}) as model:
   a=self.w.local_chat.send('fixture-01','你好',False);b=self.w.local_chat.send('fixture-02','你还记得吗',True)
   self.assertEqual(a['memory_used'],0);self.assertEqual(b['memory_used'],1);self.assertEqual(b['history_turns_used'],2)
   self.assertEqual(model.call_count,2);self.assertTrue(all(call.kwargs['private_uncached'] is True for call in model.call_args_list));self.assertEqual(len(self.w.local_chat.history()['messages']),4)
   duplicate=self.w.local_chat.send('fixture-02','你还记得吗',True);self.assertEqual(duplicate['status'],'already_recorded');self.assertEqual(model.call_count,2)
   with self.assertRaises(Fault):self.w.local_chat.send('fixture-02','不同内容',True)
  self.assertEqual(self.http.calls,[])
 def test_topic_match_can_include_older_owner_memory_only_when_selected(self):
  self.enable_budget();control=self.w.growth.controls()
  control.remember(Scope.owner(),'主人喜欢茉莉花','主人确认',1.0)
  for i in range(12):control.remember(Scope.owner(),'一般记录'+str(i),'主人确认',1.0)
  control.remember(Scope.friend('fixture-friend'),'外部朋友喜欢茉莉花','主人确认',1.0,public_approved=True)
  with patch.object(self.w.model,'chat',return_value={'text':'模拟回答','cached':False}) as model:
   selected=self.w.local_chat.send('fixture-topic-1','你记得我喜欢茉莉花吗',True)
   sent=model.call_args.args[3];self.assertTrue(any('主人喜欢茉莉花' in x['content'] for x in sent));self.assertFalse(any('外部朋友' in x['content'] for x in sent));self.assertGreater(selected['memory_used'],0)
   self.w.local_chat.send('fixture-topic-2','你记得我喜欢茉莉花吗',False);self.assertEqual(model.call_args.args[3],[])
  self.assertEqual(self.http.calls,[])
 def test_full_model_budget_path_uses_only_fixture_transport(self):
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
  self.w.s.vault.save({'api_key':'fixture-only-secret'});self.w.s.gate=lambda *args,**kwargs:None
  result=self.w.local_chat.send('fixture-04','你好',False)
  self.assertEqual(result['status'],'completed');self.assertFalse(result['platform_sent']);self.assertEqual(len([x for x in self.http.calls if '/chat/completions' in x[1]]),1)
  self.assertEqual(self.w.s.db.execute("SELECT count(*) FROM charges WHERE state='settled'").fetchone()[0],1)
  self.assertEqual(self.w.s.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'model:%'").fetchone()[0],0)
  self.assertEqual(self.w.local_chat.send('fixture-04','你好',False)['status'],'already_recorded');self.assertEqual(len([x for x in self.http.calls if '/chat/completions' in x[1]]),1)
 def test_charged_failure_is_uncertain_and_never_replayed(self):
  self.enable_budget()
  def fail(*args,**kwargs):
   self.w.s.db.execute("INSERT INTO charges VALUES (?,?,?,NULL,'uncertain',?)",('model:local:fixture-03','2026-11-04',100,'fixture'));raise Fault('模拟网络中断')
  with patch.object(self.w.model,'chat',side_effect=fail) as model:
   with self.assertRaises(Fault):self.w.local_chat.send('fixture-03','你好',False)
   result=self.w.local_chat.send('fixture-03','你好',False);self.assertEqual(result['previous_state'],'uncertain');self.assertEqual(model.call_count,1)
  self.assertEqual(self.w.local_chat.history()['messages'],[]);self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
