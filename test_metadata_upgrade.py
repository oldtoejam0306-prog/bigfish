import json,tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault,canonical
from bili_workbench.service import Workbench
from test_bili_readiness import ReadHTTP
from test_workbench import BV

class MetadataUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start()
        self.http=ReadHTTP();self.http.items=[{'bvid':BV,'title':'模拟视频'}]
        self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
        self.s.configure({'read_enabled':True,'account_mid':'123'})
        self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0')
        self.w.queue.tick()
        self.old=self.s.db.execute('SELECT * FROM browse_jobs').fetchone();self.key=self.old['key']
        assert self.old['state']=='done' and json.loads(self.old['result'])['coverage']=='仅推荐元数据'
    def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
    def configure_model(self):
        self.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
        self.s.vault.save({'api_key':'fixture-secret'})
    def retry(self):return self.w.queue.retry_job(self.key,upgrade_metadata=True,confirmation='确认重新分析元数据')
    def test_explicit_requeue_then_resume_analyzes_once(self):
        self.configure_model();before=len(self.http.calls);result=self.retry()
        self.assertTrue(result['requires_explicit_resume']);self.assertFalse(result['automatic_outbound']);self.assertFalse(self.w.queue.state()['enabled'])
        self.assertEqual(len(self.http.calls),before)
        self.assertEqual(self.s.db.execute('SELECT state FROM browse_jobs WHERE key=?',(self.key,)).fetchone()[0],'superseded')
        self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0');self.w.queue.tick()
        row=self.s.db.execute('SELECT state,result FROM browse_jobs WHERE key=?',(result['key'],)).fetchone()
        self.assertEqual(row['state'],'done');self.assertEqual(json.loads(row['result'])['result']['coverage'],'仅字幕')
        self.assertEqual(sum('/chat/completions' in call[1] for call in self.http.calls),1)
        with self.assertRaises(Fault):self.retry()
    def test_without_explicit_confirmation_never_requeues(self):
        self.configure_model()
        for kwargs in ({},{'upgrade_metadata':True},{'upgrade_metadata':False,'confirmation':'确认重新分析元数据'}):
            with self.assertRaises(Fault):self.w.queue.retry_job(self.key,**kwargs)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_jobs').fetchone()[0],1)
    def test_any_prior_charge_or_untrusted_old_result_blocks(self):
        self.configure_model();self.s.db.execute("INSERT INTO charges VALUES (?,?,?,NULL,'uncertain',?)",('model:queue:'+self.key,'2026-11-03',1,'fixture'))
        with self.assertRaises(Fault):self.retry()
        self.s.db.execute('DELETE FROM charges')
        for result in ({'coverage':'仅推荐元数据','model_called':True},{'coverage':'仅字幕','model_called':False},{}):
            self.s.db.execute('UPDATE browse_jobs SET result=? WHERE key=?',(canonical(result),self.key))
            with self.assertRaises(Fault):self.retry()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_jobs').fetchone()[0],1)
    def test_new_scheme_charge_and_account_change_block(self):
        self.configure_model();new_key=self.w.queue.key(self.s.config(),BV)
        self.s.db.execute("INSERT INTO charges VALUES (?,?,?,NULL,'uncertain',?)",('model:queue:'+new_key,'2026-11-03',1,'fixture'))
        with self.assertRaises(Fault):self.retry()
        self.s.db.execute('DELETE FROM charges');self.s.configure({'account_mid':'456'})
        with self.assertRaises(Fault):self.retry()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_jobs').fetchone()[0],1)
    def test_local_page_route_requeues_without_model_call(self):
        import threading,urllib.request
        from bili_workbench.web import Server
        self.configure_model();server=Server(('127.0.0.1',0),self.w)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        body=canonical({'key':self.key,'upgrade_metadata':True,'confirmation':'确认重新分析元数据'}).encode()
        req=urllib.request.Request(base+'/api/queue/retry',data=body,headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base},method='POST')
        try:
            before=len(self.http.calls)
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:result=json.load(response)
            self.assertTrue(result['requires_explicit_resume']);self.assertEqual(len(self.http.calls),before)
            self.assertFalse(self.w.queue.state()['enabled'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
