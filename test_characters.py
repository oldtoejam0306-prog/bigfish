import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from test_workbench import FakeHTTP

class CharacterTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.patch.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
 def tearDown(self):self.w.close();self.patch.stop();self.tmp.cleanup()
 def test_switch_preserves_personality_and_isolates_histories_and_memory(self):
  role=self.w.characters.create('测试角色','测试身份',['测试特征']);self.w.growth.controls().remember(Scope.owner(),'大肥鱼私人记忆','fixture:本人提供',0.8)
  with patch.object(self.w.model,'chat',return_value={'text':'测试回复'}) as model:
   self.w.local_chat.send('default-1','大肥鱼专属对话',True)
   self.w.characters.select(role['id'])
   self.assertEqual(self.w.local_chat.history()['messages'],[])
   with self.assertRaises(Fault):self.w.local_chat.send('custom-01','测试',True)
   self.w.local_chat.send('custom-02','新角色对话')
   self.assertEqual(self.w.relationship.snapshot()['local_conversations'],1)
   self.assertEqual(model.call_args.args[2]['data']['name'],'测试角色')
   self.assertEqual(model.call_args.args[3],[])
   self.assertEqual(model.call_args.kwargs['history'],[])
   self.assertEqual(self.w.local_chat.history()['messages'][0]['content'],'新角色对话')
   with self.assertRaises(Fault):self.w.characters.delete(role['id'])
   self.w.characters.select('default')
   self.assertEqual(self.w.relationship.snapshot()['local_conversations'],1)
   self.assertEqual(self.w.local_chat.history()['messages'][0]['content'],'大肥鱼专属对话')
   self.assertTrue(self.w.characters.delete(role['id'])['deleted'])
   with self.assertRaises(Fault):self.w.characters.select(role['id'])
   self.w.characters.restore(role['id']);self.w.characters.select(role['id'])
   self.assertEqual(self.w.local_chat.history()['messages'][0]['content'],'新角色对话')
  self.assertEqual(self.http.calls,[])
 def test_invalid_role_source_cannot_escape_history_filter(self):
  with self.assertRaises(Fault):self.w.growth.context(Scope.owner(),source_prefix='local-role:%:')
  with self.assertRaises(Fault):self.w.characters.select('../outside')
 def test_first_custom_model_turn_uses_selected_role_not_default_identity(self):
  role=self.w.characters.create('自定义角色','测试虚拟身份',['测试个性']);self.w.characters.select(role['id'])
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
  self.w.s.vault.save({'api_key':'fixture-only-secret'});self.w.s.gate=lambda *args,**kwargs:None
  self.w.local_chat.send('role-test-01','你好')
  prompt=self.http.calls[0][3]['messages'][0]['content']
  self.assertIn('你是主人在下方JSON中选择的虚拟角色',prompt)
  self.assertIn('自定义角色',prompt);self.assertNotIn('你是养成型虚拟女儿大肥鱼',prompt)

if __name__=='__main__':unittest.main()
