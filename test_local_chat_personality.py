import tempfile,unittest
from bili_workbench.runtime import Vault,canonical
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from core import Core,INITIAL
from test_workbench import FakeHTTP
from unittest.mock import patch

class LocalChatPersonalityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'});self.w.s.vault.save({'api_key':'fixture-only-secret'});self.w.s.gate=lambda *a,**k:None
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def payloads(self):return [call[3] for call in self.http.calls if call[1].endswith('/chat/completions')]
 def test_first_turn_uses_existing_personality_and_no_memory_fallback(self):
  result=self.w.local_chat.send('first-001','你好',True);msg=self.payloads()[0]['messages'];system=msg[0]['content']
  self.assertEqual(result['memory_used'],0);self.assertEqual(result['history_turns_used'],0);self.assertEqual(len(msg),2)
  for trait in INITIAL['traits']:self.assertIn(trait,system)
  self.assertIn('不要编造未提供的口癖',system);self.assertIn('"memories":[]',system.replace('owner_selected_',''))
 def test_each_turn_reloads_version_and_selected_private_memory(self):
  self.w.growth.controls().remember(Scope.owner(),'主人测试私密记忆','fixture:本人提供',0.8)
  self.w.growth.controls().remember(Scope.friend('99'),'其他好友测试记忆','fixture:公开授权',0.8,public_approved=True)
  self.w.local_chat.send('first-002','你好',True)
  core=Core(self.w.growth.root/'character.db')
  try:core.correct('fixture人格修订',{'traits':['测试版本特征'],'identity':'测试身份','creative_interest':'测试兴趣'})
  finally:core.close()
  self.w.local_chat.send('second-02','继续',True);first,second=self.payloads();s1=first['messages'][0]['content'];s2=second['messages'][0]['content']
  self.assertIn('主人测试私密记忆',s1);self.assertIn('主人测试私密记忆',s2);self.assertNotIn('其他好友测试记忆',s1+s2)
  self.assertIn('测试版本特征',s2);self.assertNotIn('清亮灵动',s2);self.assertGreater(len(second['messages']),2)
  self.assertEqual(len(self.http.calls),2)
 def test_unselected_memory_is_not_sent(self):
  self.w.growth.controls().remember(Scope.owner(),'不选择就不发送的记忆','fixture:本人提供',0.8)
  self.w.local_chat.send('first-003','你好',False);system=self.payloads()[0]['messages'][0]['content'];self.assertNotIn('不选择就不发送的记忆',system)

if __name__=='__main__':unittest.main()
