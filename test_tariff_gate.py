import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from bili_workbench.service import Workbench
from bili_workbench.tariff import status
from bili_workbench.deepseek_test import prepare,OffPeakDeferred,require_window
from bili_workbench.runtime import Fault,budget_day
from test_workbench import FakeHTTP

def utc(day,hour,minute=0):return datetime.fromisoformat(day+'T'+str(hour).zfill(2)+':'+str(minute).zfill(2)+':00+00:00')
class TariffGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
        prepare(self.w);self.s.configure({'paid_enabled':True,'daily_budget_micro':10000000,'request_cap_micro':10000});self.s.vault.save({'api_key':'fixture-secret'})
        self.w.qq.configure({'enabled':True,'bot_id':10001,'owner_id':20002})
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def event(self):return {'self_id':10001,'user_id':20002,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'message':[{'type':'text','data':{'text':'低谷再回答'}}]}
    def test_budget_day_is_chosen_after_wait_at_beijing_midnight(self):
        from datetime import timedelta
        clock=[datetime(2026,11,3,23,59,59,tzinfo=timezone(timedelta(hours=8)))]
        self.s.configure({'provider_url':'https://example.test/v1','daily_budget_micro':10000})
        self.s.db.execute("INSERT INTO charges VALUES ('old','2026-11-03',1,1,'settled','cheap')")
        def gate(*args,**kwargs):clock[0]=datetime(2026,11,4,0,0,1,tzinfo=timezone(timedelta(hours=8)))
        self.s.gate=gate
        with patch('bili_workbench.runtime.datetime') as dateclass:
            dateclass.now.side_effect=lambda *args,**kwargs:clock[0]
            self.w.model.chat('你好','midnight',{},[])
        self.assertEqual(self.s.db.execute("SELECT day FROM charges WHERE id='model:midnight'").fetchone()[0],'2026-11-04')
    def test_peak_noon_evening_and_weekend_boundaries(self):
        self.assertFalse(status(utc('2026-11-04',1))['allowed'])
        self.assertTrue(status(utc('2026-11-04',4))['allowed'])
        self.assertFalse(status(utc('2026-11-04',6))['allowed'])
        self.assertTrue(status(utc('2026-11-04',10))['allowed'])
        self.assertTrue(status(utc('2026-11-07',2))['allowed'])
    def test_holidays_unconfirmed_and_unknown_calendar_fail_closed(self):
        self.assertEqual(status(utc('2026-10-01',2))['reason'],'holiday_unconfirmed')
        self.assertFalse(status(utc('2027-01-04',2))['allowed'])
        with patch('bili_workbench.tariff.PROVIDER_CONFIRMED_HOLIDAYS',frozenset({'2026-10-01'})):
            self.assertTrue(status(utc('2026-10-01',2))['allowed'])
    def test_peak_prevents_credential_read_call_and_charge(self):
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',2)),patch.object(self.s.vault,'read',side_effect=AssertionError('no key read')):
            with self.assertRaises(OffPeakDeferred):self.w.model.chat('你好','blocked',{},[])
        self.assertEqual(self.http.calls,[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_other_provider_is_not_assumed_discounted(self):
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',2)):
            require_window('https://another-provider.invalid/v1')
    def test_qq_peak_queue_resume_once_no_sending(self):
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',2)):
            result=self.w.qq.process_fixture(self.event());self.assertEqual(result['status'],'deferred')
            self.assertTrue(self.w.qq.resume_deferred()['waiting']);self.assertEqual(self.http.calls,[])
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',4)):
            reply=self.w.qq.resume_deferred()['results'][0]
            self.assertEqual(reply['status'],'model_draft');self.assertFalse(reply['send'])
            self.assertEqual(self.w.qq.resume_deferred()['results'],[])
        self.assertEqual(len(self.http.calls),1)
    def test_bili_peak_waits_without_fetch_or_model(self):
        self.s.configure({'account_mid':'123','read_enabled':True});self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0')
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',2)):self.w.queue.tick()
        state=self.w.queue.state();self.assertTrue(state['enabled']);self.assertEqual(state['jobs'],[]);self.assertEqual(self.http.calls,[])
    def test_no_automatic_budget_raise_and_no_currency_guess(self):
        self.s.configure({'daily_budget_micro':10000001})
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',4)):
            self.assertFalse(self.w.model.plan()['ready'])
            with self.assertRaises(Fault):self.w.model.chat('你好','overspend',{},[])
        self.assertEqual(self.http.calls,[])
        self.assertEqual(budget_day({'budget_day_timezone':'Asia/Shanghai'},utc('2026-11-03',17)),'2026-11-04')
    def test_crossing_peak_after_reserve_keeps_proven_zero_no_call_and_resumes_same_id(self):
        times=iter([utc('2026-11-04',0,59),utc('2026-11-04',0,59),utc('2026-11-04',1)])
        with patch('bili_workbench.deepseek_test.utc_now',side_effect=lambda:next(times)):
            with self.assertRaises(OffPeakDeferred):self.w.model.chat('你好','cross',{},[])
        self.assertEqual(self.http.calls,[])
        row=self.s.db.execute('SELECT state,actual,reserved FROM charges').fetchone();self.assertEqual(tuple(row),('settled',0,0))
        with patch('bili_workbench.deepseek_test.utc_now',return_value=utc('2026-11-04',4)):
            self.assertEqual(self.w.model.chat('你好','cross',{},[])['usage']['input_tokens'],100)
        self.assertEqual(len(self.http.calls),1)

if __name__=='__main__':unittest.main()
