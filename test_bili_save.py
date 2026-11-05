import json,tempfile,threading,unittest,urllib.request
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class BiliSaveTest(unittest.TestCase):
 def test_partial_account_save_preserves_security_flags(self):
  with tempfile.TemporaryDirectory() as d,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(d,http);server=Server(('127.0.0.1',0),w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
   try:
    req=urllib.request.Request(base+'/api/config',data=canonical({'config':{'account_mid':'123456','read_enabled':True}}).encode(),headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base},method='POST')
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:self.assertTrue(json.load(response)['saved'])
    c=w.s.config();self.assertEqual(c['account_mid'],'123456');self.assertTrue(c['read_enabled']);self.assertFalse(c['paid_enabled']);self.assertFalse(c['outbound_enabled']);self.assertEqual(http.calls,[])
   finally:server.shutdown();server.server_close();thread.join();w.close()

if __name__=='__main__':unittest.main()
