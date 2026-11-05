import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class EmotionTests(unittest.TestCase):
 def test_opt_in_success_failure_decay_and_manual_override(self):
  with tempfile.TemporaryDirectory() as root, patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   try:
    e=w.emotion
    self.assertIsNone(e.predict('default','真开心',now=10000))
    e.configure('default',True)
    self.assertEqual(e.predict('default','不开心',now=10000),'温柔')
    e.record('default','温柔',now=10000)
    self.assertEqual(e.predict('default','你好',now=13599),'温柔')
    self.assertEqual(e.predict('default','你好',now=13600),'跟随人设')
    self.assertFalse(e.status('abcdef012345abcdef012345')['enabled'])
    w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
    with patch.object(w.model,'chat',return_value={'text':'测试回复'}) as model:
     w.local_chat.send('happy-001','今天真开心')
     self.assertEqual(model.call_args.kwargs['conversation']['simulated_tone'],'轻快')
     w.relationship.set_tone('认真');w.local_chat.send('happy-002','今天很开心')
     self.assertNotIn('simulated_tone',model.call_args.kwargs['conversation'])
    with patch.object(w.model,'chat',side_effect=RuntimeError('fixture')):
     with self.assertRaises(RuntimeError):w.local_chat.send('fail-0001','我很难过')
    self.assertEqual(e.status('default')['tone'],'轻快')
    e.configure('default',False);self.assertIsNone(e.predict('default','开心'))
    self.assertEqual(http.calls,[])
   finally:w.close()

if __name__=='__main__':unittest.main()
