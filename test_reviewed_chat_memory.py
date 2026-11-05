import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from test_workbench import FakeHTTP

class ReviewedChatMemoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
  self.ids=self.w.growth.record_exchange(Scope.owner(),'local:fixture-event','我喜欢阅读','好的','local-owner:本机主人会话')
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_review_save_and_replay(self):
  history=self.w.local_chat.history()['messages'];self.assertEqual(history[0]['id'],self.ids['user'])
  plain=self.w.growth.context(Scope.owner(),source_prefix='local-owner:')['messages'];self.assertNotIn('id',plain[0])
  with patch.object(self.w.model,'chat') as model:
   result=self.w.local_chat.remember_turn('fixture-01',self.ids['user'],'主人喜欢阅读','确认保存为主人记忆')
   self.assertEqual(result['status'],'completed');self.assertFalse(result['model_called'])
   again=self.w.local_chat.remember_turn('fixture-01',self.ids['user'],'主人喜欢阅读','确认保存为主人记忆');self.assertEqual(again['previous_state'],'completed')
   with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-01',self.ids['user'],'另一个内容','确认保存为主人记忆')
   model.assert_not_called()
  memories=self.w.growth.context(Scope.owner(),include_memory=True)['memories'];self.assertEqual([x['content'] for x in memories],['主人喜欢阅读']);self.assertEqual(self.http.calls,[])
 def test_rejects_other_roles_unconfirmed_and_deleted(self):
  with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-02',self.ids['assistant'],'角色生成','确认保存为主人记忆')
  with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-03',self.ids['user'],'未确认','')
  foreign=self.w.growth.record_exchange(Scope.friend('someone'),'foreign','私聊消息','好','local-owner:伪造来源')
  with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-04',foreign['user'],'其他会话','确认保存为主人记忆')
  with self.w.growth._db() as db:db.execute('UPDATE growth_turns SET deleted=1 WHERE id=?',(self.ids['user'],));db.commit()
  with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-05',self.ids['user'],'已删除','确认保存为主人记忆')
  self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'],[])
 def test_uncertain_write_is_not_replayed(self):
  with patch.object(self.w.growth,'controls') as controls:
   controls.return_value.remember.side_effect=RuntimeError('simulated unknown')
   with self.assertRaises(RuntimeError):self.w.local_chat.remember_turn('fixture-06',self.ids['user'],'内容','确认保存为主人记忆')
   again=self.w.local_chat.remember_turn('fixture-06',self.ids['user'],'内容','确认保存为主人记忆')
   self.assertEqual(again['previous_state'],'uncertain');self.assertEqual(controls.return_value.remember.call_count,1)
 def test_owner_can_page_correct_and_delete_older_memories(self):
  control=self.w.growth.controls();owner=Scope.owner()
  ids=[control.remember(owner,'第'+str(i)+'条','主人确认',1.0) for i in range(25)]
  friend=Scope.friend('fixture-friend');control.remember(friend,'不属于主人','主人确认',1.0,public_approved=True)
  first=self.w.growth_action({'action':'list_memories'});self.assertEqual(len(first['memories']),20);self.assertTrue(first['has_more'])
  self.assertNotIn('不属于主人',[x['content'] for x in first['memories']])
  second=self.w.growth_action({'action':'list_memories','before_id':first['next_before_id']});self.assertEqual(len(second['memories']),5);self.assertFalse(second['has_more'])
  control.correct_memory(owner,ids[0],'纠正后的记忆','主人纠正');control.delete_memory(owner,ids[1],'主人删除')
  reread=control.list_memories(owner,before_id=first['next_before_id']);self.assertIn('纠正后的记忆',[x['content'] for x in reread['memories']]);self.assertNotIn(ids[1],[x['id'] for x in reread['memories']])
  for bad in (True,0,-1,'1'):
   with self.assertRaises(Fault):control.list_memories(owner,before_id=bad)
  self.assertEqual(self.http.calls,[])
 def test_custom_character_cannot_save_owner_memory(self):
  with patch.object(self.w.characters,'current',return_value={'id':'a'*24,'name':'fixture'}):
   with self.assertRaises(Fault):self.w.local_chat.remember_turn('fixture-07',self.ids['user'],'内容','确认保存为主人记忆')
  self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'],[])

if __name__=='__main__':unittest.main()
