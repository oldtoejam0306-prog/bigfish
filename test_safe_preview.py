import json,sys,tempfile,threading,unittest,urllib.error,urllib.request
from unittest.mock import patch
from bili_workbench.runtime import canonical
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class SafePreviewTests(unittest.TestCase):
    def test_preview_rejects_all_posts_and_exposes_no_platform_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            http=FakeHTTP();work=Workbench(tmp,http);server=Server(('127.0.0.1',0),work,preview=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(base+'/',timeout=5) as response:page=response.read().decode()
                self.assertIn('data-preview="true"',page);self.assertIn("dataset.preview==='true'",page)
                headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base}
                for route,payload in (('/api/config',{'config':{'paid_enabled':True}}),('/api/test-model',{}),('/onebot/event',{})):
                    req=urllib.request.Request(base+route,data=canonical(payload).encode(),headers=headers,method='POST')
                    with self.assertRaises(urllib.error.HTTPError) as err:opener.open(req,timeout=5)
                    self.assertEqual(err.exception.code,403)
                self.assertFalse(work.s.config()['paid_enabled']);self.assertEqual(http.calls,[])
                self.assertEqual(work.s.db.execute('SELECT count(*) FROM qq_native_inbox').fetchone()[0],0)
            finally:server.shutdown();server.server_close();thread.join();work.close()
    def test_launcher_uses_new_directory_random_port_and_preview_flag(self):
        from bili_workbench.safe_preview import main
        with tempfile.TemporaryDirectory() as tmp,patch('bili_workbench.safe_preview.tempfile.mkdtemp',return_value=tmp),patch('bili_workbench.safe_preview.web.main') as run:
            captured=[];run.side_effect=lambda:captured.extend(sys.argv)
            main()
            self.assertIn('--preview',captured);self.assertEqual(captured[captured.index('--port')+1],'0');self.assertEqual(captured[captured.index('--data-dir')+1],tmp)

if __name__=='__main__':unittest.main()
