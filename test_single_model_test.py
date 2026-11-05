import tempfile
import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from bili_workbench.deepseek_test import prepare
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

LOW=datetime(2026,11,3,10,tzinfo=timezone.utc)
HIGH=datetime(2026,11,3,7,tzinfo=timezone.utc)
class SingleModelTest(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.p=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.p.start()
        self.http=FakeHTTP();self.w=Workbench(self.t.name,self.http);self.w.s.gate=lambda *a,**k:None
        prepare(self.w)
    def tearDown(self):self.w.close();self.p.stop();self.t.cleanup()
    def enable(self):self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000000,'request_cap_micro':100000})
    def run_test(self,ident='fixture01',confirmation='确认执行一次模型测试'):
        c=self.w.s.config();return self.w.single_model_test(ident,confirmation,{k:c[k] for k in ('model','daily_budget_micro','request_cap_micro')},'fixture-only-secret-123')
    def test_blocks_without_confirmation_budget_or_window(self):
        with patch('bili_workbench.deepseek_test.utc_now',return_value=LOW):
            with self.assertRaises(Fault):self.run_test(confirmation='')
            with self.assertRaises(Fault):self.run_test()
            self.enable();self.w.s.configure({'request_cap_micro':1})
            with self.assertRaises(Fault):self.run_test()
        self.enable()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=HIGH):
            with self.assertRaises(Fault):self.run_test()
        self.assertEqual(self.http.calls,[])
    def test_approved_once_no_qq_bili_or_replay(self):
        self.enable()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=LOW):
            result=self.run_test()
            with self.assertRaises(Fault):self.run_test()
            with self.assertRaises(Fault):self.run_test('fixture02')
        self.assertFalse(result['sent_to_qq_or_bili']);self.assertFalse(result['automatic_retry'])
        self.assertEqual(len(self.http.calls),1);self.assertTrue(self.http.calls[0][1].endswith('/chat/completions'))
    def test_unknown_result_blocks_repeat(self):
        self.enable();self.http.usage=False
        with patch('bili_workbench.deepseek_test.utc_now',return_value=LOW):
            with self.assertRaises(Fault):self.run_test()
            with self.assertRaises(Fault):self.run_test()
            with self.assertRaises(Fault):self.run_test('fixture02')
        self.assertEqual(len(self.http.calls),1)
        self.assertEqual(self.w.s.db.execute("SELECT state FROM charges").fetchone()[0],'uncertain')
    def test_changed_budget_rejected_before_network(self):
        self.enable();c=self.w.s.config()
        with patch('bili_workbench.deepseek_test.utc_now',return_value=LOW):
            with self.assertRaises(Fault):self.w.single_model_test('fixture01','确认执行一次模型测试',{'model':c['model'],'daily_budget_micro':1,'request_cap_micro':100000},'fixture-only-secret-123')
        self.assertEqual(self.http.calls,[])
    def test_ephemeral_key_never_reads_or_writes_vault_or_data_files(self):
        from pathlib import Path
        self.enable()
        with patch.object(self.w.s.vault,'read',side_effect=AssertionError('vault read')),patch.object(self.w.s.vault,'save',side_effect=AssertionError('vault write')),patch('bili_workbench.deepseek_test.utc_now',return_value=LOW):
            result=self.run_test()
        self.assertFalse(result['sent_to_qq_or_bili'])
        for f in Path(self.t.name).rglob('*'):
            if f.is_file():self.assertNotIn(b'fixture-only-secret-123',f.read_bytes())
    def test_local_http_does_not_echo_key(self):
        import json,threading,urllib.request
        from bili_workbench.web import Server
        self.enable();server=Server(('127.0.0.1',0),self.w)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        c=self.w.s.config();url='http://127.0.0.1:'+str(server.server_port)
        data={'request_id':'fixture01','confirmation':'确认执行一次模型测试','expected':{k:c[k] for k in ('model','daily_budget_micro','request_cap_micro')},'ephemeral_key':'fixture-only-secret-123'}
        request=urllib.request.Request(url+'/api/single-model-test',data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':url},method='POST')
        try:
            with patch('bili_workbench.deepseek_test.utc_now',return_value=LOW),patch.object(self.w.s.vault,'read',side_effect=AssertionError('vault read')):
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request,timeout=5) as response:body=response.read()
            self.assertNotIn(b'fixture-only-secret-123',body)
            self.assertEqual(len(self.http.calls),1)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
