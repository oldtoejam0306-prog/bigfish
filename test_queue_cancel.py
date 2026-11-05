import json,tempfile,threading,time,unittest,urllib.request
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class QueueCancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
        self.w.s.db.execute('UPDATE browse_control SET enabled=1,next_at=?',(time.time()+3600,))
    def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
    def test_cancel_event_pauses_even_before_scheduled_tick_without_network(self):
        self.w.cancel_event.set();self.w.queue.tick();state=self.w.queue.state()
        self.assertEqual(state['enabled'],0);self.assertIn('取消',state['reason']);self.assertEqual(self.http.calls,[])
    def test_cancel_api_pauses_queue_and_resume_does_not_restart_it(self):
        server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        def post(path):
            req=urllib.request.Request(base+'/api/'+path,data=canonical({}).encode(),headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base},method='POST')
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:return json.load(response)
        try:
            self.assertTrue(post('cancel')['queue_paused']);self.assertEqual(self.w.queue.state()['enabled'],0);self.assertTrue(self.w.cancel_event.is_set())
            post('resume');self.assertFalse(self.w.cancel_event.is_set());self.assertEqual(self.w.queue.state()['enabled'],0);self.assertEqual(self.http.calls,[])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
