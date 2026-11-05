import json,tempfile,threading,unittest,urllib.request,urllib.error
from unittest.mock import patch
from bili_workbench.runtime import Vault,canonical
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class LocalChatRouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});self.servers=[]
 def tearDown(self):
  for server,thread in self.servers:server.shutdown();server.server_close();thread.join()
  self.w.close();self.vault.stop();self.tmp.cleanup()
 def request(self,preview=False,auth=True,method='POST',path_override=None,payload=None):
  server=Server(('127.0.0.1',0),self.w,preview=preview);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread));base='http://127.0.0.1:'+str(server.server_port);headers={'Origin':base}
  if auth:headers['X-Local-Token']=server.token
  if method=='POST':headers['Content-Type']='application/json';body=canonical({'request_id':'fixture-abc','text':'模拟对话','include_memory':False}).encode();path='/api/local-chat/send'
  else:body=None;path='/api/local-chat/history'
  if path_override is not None:path=path_override;body=canonical(payload).encode()
  req=urllib.request.Request(base+path,data=body,headers=headers,method=method)
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:return json.load(response)
 def test_authorized_fixture_end_to_end(self):
  with patch.object(self.w.model,'chat',return_value={'text':'模拟回答','cached':False}) as model:
   result=self.request();self.assertEqual(result['reply'],'模拟回答');self.assertFalse(result['platform_sent']);self.assertEqual(model.call_count,1)
   history=self.request(method='GET');self.assertEqual([x['content'] for x in history['messages']],['模拟对话','模拟回答']);self.assertEqual(self.http.calls,[])
 def test_reviewed_memory_route_requires_authorized_nonpreview_session(self):
  ids=self.w.growth.record_exchange(Scope.owner(),'local:fixture-route','主人原话','模拟回复','local-owner:本机主人会话')
  payload={'request_id':'fixture-review-01','turn_id':ids['user'],'content':'主人原话','confirmation':'确认保存为主人记忆'}
  for options in ({'preview':True},{'auth':False}):
   with self.assertRaises(urllib.error.HTTPError) as e:self.request(path_override='/api/local-chat/remember-turn',payload=payload,**options)
   self.assertEqual(e.exception.code,403)
  result=self.request(path_override='/api/local-chat/remember-turn',payload=payload);self.assertEqual(result['status'],'completed')
  self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'][0]['content'],'主人原话');self.assertEqual(self.http.calls,[])
 def test_preview_and_unauthorized_post_never_call_model(self):
  with patch.object(self.w.model,'chat') as model:
   for options in ({'preview':True},{'auth':False}):
    with self.assertRaises(urllib.error.HTTPError) as e:self.request(**options)
    self.assertEqual(e.exception.code,403)
   model.assert_not_called()

if __name__=='__main__':unittest.main()
