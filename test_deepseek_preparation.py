import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from bili_workbench.service import Workbench
from bili_workbench.runtime import Fault
from bili_workbench.deepseek_test import window,prepare
from test_workbench import FakeHTTP

BEFORE=datetime(2026,11,3,9,59,59,tzinfo=timezone.utc)
AFTER=datetime(2026,11,3,10,tzinfo=timezone.utc)
class DeepSeekPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def authorize_fixture(self):
        prepare(self.w);self.s.configure({'paid_enabled':True,'request_cap_micro':10000,'daily_budget_micro':100000});self.s.vault.save({'api_key':'fixture-secret'})
    def event(self,user=20002):return {'self_id':10001,'user_id':user,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'message':[{'type':'text','data':{'text':'你好'}}]}
    def test_time_boundary_and_timezone_conversion(self):
        self.assertFalse(window(BEFORE)['time_allowed']);self.assertTrue(window(AFTER)['time_allowed'])
        self.assertTrue(window(datetime(2026,11,3,18,tzinfo=timezone(timedelta(hours=8))))['time_allowed'])
        with self.assertRaises(Fault):window(datetime(2026,11,3,18))
    def test_prepare_is_zero_network_zero_budget_without_touching_vault(self):
        self.w.qq.configure({'social_mode':'account_social'})
        with patch.object(self.s.vault,'save',side_effect=AssertionError('must not save credential')),patch.object(self.s.vault,'read',side_effect=AssertionError('must not read credential')):
            result=prepare(self.w)
        self.assertFalse(self.s.config()['paid_enabled']);self.assertEqual(self.s.config()['daily_budget_micro'],0)
        self.assertFalse(self.s.config()['outbound_enabled']);self.assertEqual(self.http.calls,[])
        self.assertEqual(result['expected_max_per_call_micro_at_configured_ceiling'],5120)
        self.assertTrue(self.w.qq.config()['acceptance_owner_only']);self.assertEqual(self.w.qq.config()['social_mode'],'account_social')
    def test_before_window_blocks_before_credential_read_and_reservation(self):
        self.authorize_fixture()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=BEFORE),patch.object(self.s.vault,'read',side_effect=AssertionError('no early key read')):
            with self.assertRaises(Fault):self.w.model.complete('你好','early')
        self.assertEqual(self.http.calls,[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_after_window_alone_does_not_enable_paid_calls(self):
        prepare(self.w)
        with patch('bili_workbench.deepseek_test.utc_now',return_value=AFTER):
            with self.assertRaises(Fault):self.w.model.complete('你好','no-budget')
        self.assertEqual(self.http.calls,[])
    def test_after_window_fixture_uses_non_thinking_no_tools_real_usage_contract(self):
        self.authorize_fixture()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=AFTER):result=self.w.model.chat('你好','allowed',{},[])
        request=self.http.calls[0][3]
        self.assertEqual(request['model'],'deepseek-flash');self.assertEqual(request['thinking'],{'type':'disabled'});self.assertEqual(request['tool_choice'],'none')
        self.assertNotIn('tools',request);self.assertEqual(result['usage'],{'input_tokens':100,'output_tokens':20})
    def test_owner_acceptance_scope_keeps_social_authorization_but_denies_other_friend(self):
        self.authorize_fixture();self.w.qq.configure({'enabled':True,'bot_id':10001,'owner_id':20002})
        with patch('bili_workbench.deepseek_test.utc_now',return_value=AFTER):
            with self.assertRaises(Fault):self.w.qq.process_fixture(self.event(30003))
            self.assertEqual(self.http.calls,[])
            self.assertEqual(self.w.qq.process_fixture(self.event())['status'],'model_draft')
        self.assertEqual(self.w.qq.config()['social_mode'],'account_social')
    def test_plan_reports_time_gate_no_inference(self):
        self.authorize_fixture()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=BEFORE):
            self.assertFalse(self.w.model.plan()['ready'])
        self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
