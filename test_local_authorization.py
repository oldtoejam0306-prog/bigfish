import json,tempfile,threading,unittest,urllib.error,urllib.request
from unittest.mock import patch
from bili_workbench.runtime import Vault
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class LocalAuthorizationTests(unittest.TestCase):
 def test_unauthorized_and_preview_cannot_start_monitor_or_launch_app(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   w=Workbench(root,FakeHTTP())
   try:
    with patch.object(w.system_activity,'start') as start,patch.object(w.computer_actions,'execute') as execute:
     for preview,auth in ((False,False),(True,True)):
      server=Server(('127.0.0.1',0),w,preview=preview);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
      try:
       base='http://127.0.0.1:'+str(server.server_port);headers={'Content-Type':'application/json'}
       if auth:headers['X-Local-Token']=server.token
       for path in ('/api/system-activity/start','/api/computer-actions/execute'):
        req=urllib.request.Request(base+path,data=json.dumps({'id':'fixture'}).encode(),headers=headers)
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5)
        self.assertEqual(error.exception.code,403)
      finally:server.shutdown();server.server_close();thread.join()
     start.assert_not_called();execute.assert_not_called()
   finally:w.close()

if __name__=='__main__':unittest.main()
