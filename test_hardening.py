import json,tempfile,unittest,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from bili_workbench.service import Workbench
from bili_workbench.runtime import Fault,canonical
from bili_workbench.deepseek_test import OffPeakDeferred
from bili_workbench.doctor import diagnose
from test_workbench import FakeHTTP,BV
from test_workflows import FlowHTTP

class MutableHTTP(FakeHTTP):
    def __init__(self):super().__init__();self.mutate=lambda url,data:data
    def json(self,method,url,headers=None,payload=None,form=False):
        if '/popular' in url:
            self.calls.append((method,url,headers,payload));data={'code':0,'data':{'list':[]}}
        else:data=super().json(method,url,headers,payload,form)
        return self.mutate(url,data)
class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=MutableHTTP();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
        self.s.configure({'read_enabled':True,'account_mid':'123'})
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def configure(self):
        self.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
        self.s.vault.save({'api_key':'fixture-secret'})
    def test_boolean_or_object_error_codes_are_not_success_or_echoed(self):
        for code in (False,{'secret':'must-not-echo'}):
            self.http.mutate=lambda url,data:dict(data,code=code) if '/popular' in url else data
            with self.assertRaises(Fault) as error:self.w.bili.feed()
            self.assertNotIn('must-not-echo',str(error.exception))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'bili:%'").fetchone()[0],0)
    def test_live_metadata_guards_identity_types_bounds_and_part_binding(self):
        for change in ({'aid':True},{'cid':0},{'bvid':'BV0000000001'},{'title':[]},{'duration':-20},{'owner':{'name':'name'}},{'stat':{'view':'bad'}},{'pages':[{'cid':999}]}):
            self.s.db.execute('DELETE FROM cache')
            self.http.mutate=lambda url,data:dict(data,data=dict(data['data'],**change)) if '/wbi/view' in url else data
            with self.subTest(change=change),self.assertRaises(Fault):self.w.bili.info(BV)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM cache WHERE key LIKE 'bili:%'").fetchone()[0],0)
    def test_sanitized_info_cache_keeps_original_ttl_and_drops_unknown_structure(self):
        self.http.mutate=lambda url,data:dict(data,data=dict(data['data'],unexpected={'secret':'do-not-cache'})) if '/wbi/view' in url else data
        with patch('time.time',return_value=1000000):result=self.w.bili.info(BV)
        self.assertNotIn('unexpected',result);row=self.s.db.execute("SELECT value,expires FROM cache WHERE key LIKE 'bili:%'").fetchone();self.assertNotIn('do-not-cache',row['value'])
        before=len(self.http.calls)
        with patch('time.time',return_value=1000100):self.w.bili.info(BV)
        self.assertEqual(len(self.http.calls),before);self.assertEqual(self.s.db.execute("SELECT expires FROM cache WHERE key LIKE 'bili:%'").fetchone()[0],row['expires'])
    def test_bad_subtitle_times_do_not_enter_materials_or_subtitle_cache(self):
        for value in (False,float('nan'),-1,{'nested':'bad'}):
            self.http.mutate=lambda url,data:dict(data,body=[{'from':value,'to':1,'content':'fixture'}]) if 'sub.json' in url else data
            with self.subTest(value=value),self.assertRaises(Fault):self.w.bili.subtitle(BV)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM materials').fetchone()[0],0)
    def test_boolean_bili_write_ack_becomes_uncertain_and_not_resent(self):
        self.s.configure({'outbound_enabled':True});self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture','csrf':'fixture'})
        self.http.mutate=lambda url,data:dict(data,code=False) if '/reply/add' in url else data
        draft=self.w.draft('comment',{'bvid':BV,'text':'模拟评论'});self.w.review(draft['id'],'approve')
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        n=len(self.http.calls);self.assertEqual(self.w.draft_detail(draft['id'])['state'],'uncertain')
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(len(self.http.calls),n)
    def test_bili_receipt_only_retains_message_id(self):
        self.s.configure({'outbound_enabled':True});self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture','csrf':'fixture'})
        self.http.mutate=lambda url,data:dict(data,data=dict(data['data'],unexpected='must-not-retain')) if '/reply/add' in url else data
        draft=self.w.draft('comment',{'bvid':BV,'text':'模拟评论'});self.w.review(draft['id'],'approve');receipt=self.w.execute(draft['id'],'确认发送')
        self.assertEqual(receipt['receipt'],{'rpid':'123456'});self.assertNotIn('must-not-retain',canonical(self.w.draft_detail(draft['id'])))
    def test_unpaid_activity_can_explicitly_retry_same_id_after_read_failure(self):
        self.configure();transport=FlowHTTP();self.w.http=transport;self.w.bili.http=transport;self.w.model.http=transport;transport.fail=True
        self.assertEqual(self.w.activities.comment_reply(BV,'9','recover')['status'],'blocked')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        transport.fail=False;self.w.activities.retry('recover');result=self.w.activities.resume()['results'][0]
        self.assertEqual(result['status'],'drafted');self.assertFalse(result['published'])
        with self.assertRaises(Fault):self.w.activities.retry('recover')
    def test_comment_page_is_explicit_and_preserved_in_source(self):
        self.configure();transport=FlowHTTP();self.w.bili.http=transport;self.w.model.http=transport
        result=self.w.activities.comment_reply(BV,'9','page-two',2)
        self.assertEqual(result['status'],'drafted');self.assertEqual(result['source']['page'],2)
        self.assertTrue(any('pn=2' in item[1] for item in transport.calls))
        with self.assertRaises(Fault):self.w.activities.comment_reply(BV,'9','bad-page',True)
    def test_automatic_worker_never_resumes_fixture_inputs(self):
        self.configure();self.s.configure({'qq_endpoint':'http://127.0.0.1:9999','qq_transport_validated':True,'qq_auto_process':True})
        self.s.vault.save({'api_key':'fixture-secret','qq_access_token':'fixture-access-token-0000','qq_event_secret':'fixture-event-secret-0000'})
        self.w.qq.configure({'enabled':True,'bot_id':10001,'owner_id':20002})
        event={'self_id':10001,'user_id':20002,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'message':[{'type':'text','data':{'text':'只限人工恢复的fixture'}}]}
        wait=OffPeakDeferred({'resume_at_utc':'2026-11-04T10:00:00+00:00','defer_reason':'fixture'})
        with patch('bili_workbench.deepseek_test.require_window',side_effect=wait):self.assertEqual(self.w.qq.process_fixture(event)['status'],'deferred')
        self.w.interaction.tick();self.assertEqual(self.http.calls,[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM qq_deferred_inputs').fetchone()[0],1)
    def test_model_list_rechecks_window_after_rate_wait(self):
        self.s.configure({'provider_url':'https://api.deepseek.com'});self.s.vault.save({'api_key':'fixture-secret'})
        stamps=iter([datetime(2026,11,4,0,59,tzinfo=timezone.utc),datetime(2026,11,4,1,tzinfo=timezone.utc)])
        with patch('bili_workbench.deepseek_test.utc_now',side_effect=lambda:next(stamps)),self.assertRaises(OffPeakDeferred):self.w.model.test()
        self.assertEqual(self.http.calls,[])
    def test_doctor_does_not_read_credentials_or_edit_database(self):
        self.s.vault.path.write_bytes(b'not-a-vault-must-never-be-read')
        before=(Path(self.tmp.name)/'workbench.db').read_bytes()
        with patch.object(self.s.vault,'read',side_effect=AssertionError('no secret read')):report=diagnose(self.tmp.name)
        self.assertTrue(report['credentials_file_present']);self.assertFalse(report['credentials_read']);self.assertFalse(report['network_called']);self.assertEqual(report['database']['state'],'readable')
        self.assertEqual((Path(self.tmp.name)/'workbench.db').read_bytes(),before)
    def test_doctor_cli_does_not_create_missing_profile(self):
        import subprocess,sys
        directory=Path(self.tmp.name)/'absent'
        result=subprocess.run([sys.executable,'-m','bili_workbench.doctor','--data-dir',str(directory)],capture_output=True,text=True,check=True,timeout=10)
        report=json.loads(result.stdout);self.assertFalse(report['data_changed']);self.assertFalse(directory.exists())
    def test_stop_helper_explicitly_disables_environment_proxy(self):
        from bili_workbench.control import stop
        self.s.vault.save({'web_token':'fixture-session-token','web_port':8765})
        class Reply:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"stopping":true}'
        with patch('bili_workbench.control.urllib.request.build_opener') as builder:
            builder.return_value.open.return_value=Reply();self.assertTrue(stop(self.tmp.name)['stopping'])
            self.assertTrue(any(isinstance(x,urllib.request.ProxyHandler) and x.proxies=={} for x in builder.call_args.args))

if __name__=='__main__':unittest.main()
