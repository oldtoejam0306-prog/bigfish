import json
import tempfile
import unittest
from pathlib import Path
from bili_workbench.service import Workbench
from bili_workbench.runtime import Fault
from bili_workbench.presets import apply_preset,setup_check
from test_workbench import FakeHTTP,BV

class UploadHTTP(FakeHTTP):
    def __init__(self):super().__init__();self.puts=0;self.archives=0;self.fail_archive=False;self.cancel_callback=None;self.bad_host=False
    def json(self,method,url,headers=None,payload=None,form=False):
        if '/preupload?' in url:
            self.calls.append((method,url,headers,payload))
            return {'OK':1,'endpoint':'https://evil.test' if self.bad_host else '//upos-test.bilivideo.com','upos_uri':'upos://bucket/test.mp4','auth':'fixture-upload-secret','chunk_size':1048576,'biz_id':1}
        if 'upos-test' in url:
            self.calls.append((method,url,headers,payload));return {'OK':1,'upload_id':'fixture-id'}
        if '/x/vu/web/add?' in url:
            self.archives+=1
            if self.fail_archive:raise Fault('mock unknown timeout')
            return {'code':0,'data':{'bvid':BV,'aid':10}}
        return super().json(method,url,headers,payload,form)
    def request(self,method,url,headers=None,body=None,limit=4000000):
        if method=='PUT':
            self.puts+=1;self.calls.append((method,url,headers,None))
            if self.cancel_callback:self.cancel_callback()
            return b'MULTIPART_PUT_SUCCESS',{}
        raise AssertionError('unexpected transport')

class UploadQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=UploadHTTP();self.w=Workbench(self.tmp.name,self.http)
        self.s=self.w.s;self.s.configure({'account_mid':'123','read_enabled':True,'outbound_enabled':True})
        self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture','csrf':'fixture'});self.s.gate=lambda *a,**k:None
        self.file=Path(self.tmp.name)/'v.mp4';self.file.write_bytes(b'fixture-video')
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def draft(self):return self.w.draft('video',{'file':str(self.file),'title':'测试视频','text':'说明','tid':1,'tags':'测试','copyright':1})
    def test_upload_dry_run_then_single_reviewed_submission(self):
        draft=self.draft();self.assertEqual(self.http.calls,[])
        self.w.review(draft['id'],'approve');result=self.w.execute(draft['id'],'确认发送')
        self.assertEqual(result['receipt']['bvid'],BV);self.assertEqual(self.http.puts,1);self.assertEqual(self.http.archives,1)
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        with self.assertRaises(Fault):self.draft()
        self.assertEqual(self.http.archives,1)
        self.assertNotIn('fixture-upload-secret',json.dumps(self.w.draft_detail(draft['id'])))
    def test_boolean_preupload_ok_is_rejected_before_any_upload(self):
        original=self.http.json
        def malformed(*args,**kwargs):
            result=original(*args,**kwargs)
            if '/preupload?' in args[1]:result['OK']=True
            return result
        self.http.json=malformed
        draft=self.draft();self.w.review(draft['id'],'approve')
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(self.http.puts,0);self.assertEqual(self.http.archives,0)
        self.assertEqual(self.w.draft_detail(draft['id'])['state'],'uncertain')
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
    def test_unknown_submission_no_replay(self):
        draft=self.draft();self.w.review(draft['id'],'approve');self.http.fail_archive=True
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(self.w.draft_detail(draft['id'])['state'],'uncertain')
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(self.http.archives,1)
    def test_cancel_after_chunk_stops_before_publish(self):
        draft=self.draft();self.w.review(draft['id'],'approve');self.http.cancel_callback=self.w.cancel_event.set
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(self.http.archives,0);self.assertEqual(self.w.draft_detail(draft['id'])['state'],'uncertain')
    def test_upload_cdn_nonstandard_port_is_rejected(self):
        from bili_workbench.upload import valid_upload_target
        with self.assertRaises(Fault):valid_upload_target({'endpoint':'https://upos-test.bilivideo.com:8443','upos_uri':'upos://bucket/test.mp4','auth':'fixture'})
    def test_upload_host_validation(self):
        draft=self.draft();self.w.review(draft['id'],'approve');self.http.bad_host=True
        with self.assertRaises(Fault):self.w.execute(draft['id'],'确认发送')
        self.assertEqual(self.http.puts,0)
    def test_queue_dedup_daily_limit_pause_and_restart(self):
        original=self.http.json
        self.http.json=lambda *a,**k:original(*a,**k) if '/nav' in a[1] else {'code':0,'data':{'list':[{'bvid':BV,'title':'外部素材'},{'bvid':'BV0000000001','title':'二'}]}}
        self.s.configure({'browse_daily_limit':1})
        self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0')
        self.w.queue.tick();self.assertEqual(len(self.w.queue.state()['jobs']),1)
        self.s.db.execute('UPDATE browse_control SET next_at=0');self.w.queue.tick()
        self.assertEqual(len(self.w.queue.state()['jobs']),1);self.assertFalse(self.w.queue.state()['enabled'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        self.w.queue.pause();self.assertTrue(self.w.cancel_event.is_set())
        self.w.close();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s
        self.assertFalse(self.w.queue.state()['enabled']);self.assertEqual(len(self.w.queue.state()['jobs']),1)
    def test_queue_error_pauses_without_retry(self):
        self.http.fail=True;self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0')
        self.w.queue.tick();calls=len(self.http.calls)
        self.w.queue.tick();self.assertEqual(len(self.http.calls),calls);self.assertFalse(self.w.queue.state()['enabled'])
    def test_cancel_before_analyze_makes_zero_http_calls(self):
        self.w.cancel_event.set()
        with self.assertRaises(Fault):self.w._confirmed_analyze(BV,'r')
        self.assertEqual(self.http.calls,[])
    def test_manual_charge_reconciliation_never_replays(self):
        self.s.db.execute("INSERT INTO charges VALUES ('model:old','2026-01-01',100,NULL,'uncertain','model')")
        with self.assertRaises(Fault):self.w.model.reconcile('model:old',20,'yes')
        r=self.w.model.reconcile('model:old',20,'已核对提供方账单')
        self.assertFalse(r['retry_permitted']);self.assertEqual(self.http.calls,[])
        with self.assertRaises(Fault):self.w.model.reconcile('model:old',0,'已核对提供方账单')
    def test_presets_do_not_guess_prices_or_spend(self):
        self.s.configure({'paid_enabled':True,'daily_budget_micro':10,'input_price_micro_per_million':123})
        c=apply_preset(self.s,'deepseek');self.assertFalse(c['paid_enabled']);self.assertEqual(c['input_price_micro_per_million'],0)
        with self.assertRaises(Fault):apply_preset(self.s,'aliyun-beijing')
        c=apply_preset(self.s,'aliyun-beijing','test-workspace');self.assertIn('test-workspace.cn-beijing',c['provider_url'])
        self.assertFalse(setup_check(self.w)['ready_for_configured_text_path']);self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
