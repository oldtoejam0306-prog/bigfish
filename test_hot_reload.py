import json,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical
from bili_workbench.hot_reload import blocking_reason,backend_version
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class HotReloadTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.servers=[]
 def tearDown(self):
  for server,thread in self.servers:
   if thread.is_alive():server.shutdown()
   server.server_close();thread.join(timeout=5)
  self.w.close();self.vault.stop();self.tmp.cleanup()
 def server(self,supervised=False,preview=False):
  server=Server(('127.0.0.1',0),self.w,preview=preview);server.supervised=supervised;server.backend_signature=backend_version();thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread));return server
 def request(self,s,path,method='GET',auth=True):
  base='http://127.0.0.1:'+str(s.server_port);headers={'Origin':base}
  if auth:headers['X-Local-Token']=s.token
  body=None
  if method=='POST':body=canonical({}).encode();headers['Content-Type']='application/json'
  req=urllib.request.Request(base+path,data=body,headers=headers,method=method)
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:return json.load(response)
 def test_version_requires_session_and_detects_backend_change(self):
  s=self.server();r=self.request(s,'/api/ui-version');self.assertEqual(len(r['version']),64);self.assertFalse(r['backend_changed'])
  s.backend_signature='old';self.assertTrue(self.request(s,'/api/ui-version')['backend_changed'])
  with self.assertRaises(urllib.error.HTTPError) as e:self.request(s,'/api/ui-version',auth=False)
  self.assertEqual(e.exception.code,403)
 def test_busy_queue_and_unsupervised_or_preview_refuse_restart(self):
  self.w.s.db.execute('UPDATE browse_control SET enabled=1');self.assertIsNotNone(blocking_reason(self.w));s=self.server(supervised=True)
  with self.assertRaises(urllib.error.HTTPError) as e:self.request(s,'/api/reload-service','POST')
  self.assertEqual(e.exception.code,400);self.assertFalse(s.restart_requested)
  self.w.s.db.execute('UPDATE browse_control SET enabled=0')
  for preview in (False,True):
   other=self.server(supervised=False,preview=preview)
   with self.assertRaises(urllib.error.HTTPError):self.request(other,'/api/reload-service','POST')
   self.assertFalse(other.restart_requested)
  self.assertEqual(self.http.calls,[])
 def test_safe_supervised_restart_acks_without_model_or_platform(self):
  s=self.server(supervised=True);result=self.request(s,'/api/reload-service','POST');self.assertTrue(result['restarting']);self.assertFalse(result['automatic_model_call']);self.assertTrue(s.restart_requested);self.assertEqual(self.http.calls,[])
  self.servers[-1][1].join(timeout=5);self.assertFalse(self.servers[-1][1].is_alive())

if __name__=='__main__':unittest.main()
