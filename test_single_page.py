import json,os,sqlite3,subprocess,sys,tempfile,threading,unittest,urllib.request
from pathlib import Path
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class SinglePageTests(unittest.TestCase):
    def test_setup_is_fresh_bounded_and_contains_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'new';env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
            cmd=[sys.executable,'-m','bili_workbench.single_test_setup','--data-dir',str(path)]
            first=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=15)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertFalse((path/'secrets.bin').exists())
            db=sqlite3.connect(str(path/'workbench.db'))
            try:c=json.loads(db.execute('SELECT value FROM cfg WHERE id=1').fetchone()[0])
            finally:db.close()
            self.assertEqual((c['model'],c['daily_budget_micro'],c['request_cap_micro']),('deepseek-flash',1000000,100000))
            self.assertTrue(c['paid_enabled']);self.assertFalse(c['outbound_enabled']);self.assertFalse(c['qq_auto_process']);self.assertFalse(c['qq_auto_send'])
            second=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=15)
            self.assertNotEqual(second.returncode,0)
    def test_page_is_served_without_network_or_creating_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            http=FakeHTTP();work=Workbench(tmp,http);server=Server(('127.0.0.1',0),work)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:'+str(server.server_port)+'/single-test'
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=5) as response:page=response.read().decode()
                self.assertIn('id="once-key"',page)
                self.assertIn("key.value=''",page);self.assertIn("attempted=true",page)
                self.assertIn('s.single_test_attempted===false',page)
                self.assertNotIn('fixture-only-secret-123',page);self.assertEqual(http.calls,[])
            finally:server.shutdown();server.server_close();thread.join();work.close()
    def test_status_reports_previous_attempt_even_without_charge(self):
        with tempfile.TemporaryDirectory() as tmp:
            http=FakeHTTP();work=Workbench(tmp,http)
            try:
                self.assertFalse(work.selftest()['single_test_attempted'])
                work.s.db.execute('CREATE TABLE single_model_tests(id TEXT PRIMARY KEY,state TEXT NOT NULL)')
                work.s.db.execute("INSERT INTO single_model_tests VALUES ('fixture01','uncertain_or_stopped')")
                status=work.selftest()
                self.assertTrue(status['single_test_attempted']);self.assertEqual(status['charges'],[]);self.assertEqual(http.calls,[])
            finally:work.close()

if __name__=='__main__':unittest.main()
