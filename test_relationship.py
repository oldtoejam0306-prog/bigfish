import tempfile
import unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault, Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP


class RelationshipTests(unittest.TestCase):
    def test_owner_tone_and_local_stages(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
            http=FakeHTTP();w=Workbench(root,http)
            try:
                self.assertEqual(w.relationship.snapshot()['stage'],'初识')
                self.assertEqual(w.relationship.snapshot()['tone'],'跟随人设')
                w.relationship.set_tone('温柔')
                with self.assertRaises(Fault):w.relationship.set_tone('伪造选项')
                w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
                with patch.object(w.model,'chat',return_value={'text':'测试回复'}) as model:
                    for i in range(5):w.local_chat.send('fixture-'+str(i).zfill(2),'测试对话')
                    self.assertEqual(model.call_args.kwargs['conversation']['owner_selected_tone'],'温柔')
                self.assertEqual(w.relationship.snapshot()['stage'],'熟悉')
                self.assertEqual(http.calls,[])
                with self.assertRaises(Fault):w.model.chat('x','fixture-public',{},[],conversation={'audience':'public','owner_selected_tone':'温柔'})
            finally:w.close()

    def test_selected_tone_reaches_fixture_model_without_private_leak(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
            http=FakeHTTP();w=Workbench(root,http)
            try:
                w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
                w.s.vault.save({'api_key':'fixture-only-secret'});w.s.gate=lambda *args,**kwargs:None
                w.relationship.set_tone('轻快')
                w.local_chat.send('fixture-tone','你好')
                payload=[c[3] for c in http.calls if c[1].endswith('/chat/completions')][0]
                self.assertIn('owner_selected_tone',payload['messages'][0]['content'])
                self.assertIn('轻快',payload['messages'][0]['content'])
            finally:w.close()


if __name__=='__main__':unittest.main()
