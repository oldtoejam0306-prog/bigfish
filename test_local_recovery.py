import json,shutil,sqlite3,subprocess,tempfile,threading,time,unittest,urllib.error,urllib.request
from pathlib import Path
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from bili_workbench.web import Server
from test_workbench import FakeHTTP


class LocalRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.recovery=self.w.local_chat.recovery
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def seed(self,request='recover-fixture-01',state='uncertain',character='default',charge=False):
  self.w.s.db.execute('INSERT INTO local_chat_requests VALUES (?,?,?,?)',(request,'fixture fingerprint',state,time.time()));self.w.s.db.execute('INSERT INTO local_character_requests VALUES (?,?)',(request,character))
  if charge:self.w.s.db.execute('INSERT INTO charges VALUES (?,?,?,?,?,?)',('model:local:'+request,'2026-11-04',100,None,'uncertain','fixture'))
 def test_checks_do_not_take_busy_model_lock_or_read_credentials(self):
  self.seed(state='running');result=[]
  with self.w.s.lock,patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch.object(self.w.model,'chat',side_effect=AssertionError('no model')):
   thread=threading.Thread(target=lambda:result.append(self.recovery.status('default','chat','recover-fixture-01')));thread.start();thread.join(timeout=1);self.assertFalse(thread.is_alive(),'status must not wait for shared model lock')
  thread.join();self.assertEqual(result[0]['state'],'running');self.assertEqual(self.http.calls,[])
 def test_completed_chat_and_memory_status_expose_only_outcome_metadata(self):
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  with patch.object(self.w.model,'chat',return_value={'text':'private fixture answer'}):self.w.local_chat.send('completed-fixture-01','private fixture user text')
  turn=self.w.local_chat.history()['messages'][0]['id'];self.w.local_chat.remember_turn('remember-fixture-01',turn,'private memory fixture','确认保存为主人记忆')
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   chat=self.recovery.status('default','chat','completed-fixture-01');memory=self.recovery.status('default','memory','remember-fixture-01');pending=self.recovery.pending('default')
  self.assertEqual(chat['state'],'completed');self.assertEqual(memory['state'],'completed');self.assertIsInstance(memory['memory_id'],int);self.assertEqual(pending['items'],[])
  text=json.dumps([chat,memory,pending]);self.assertNotIn('private',text);self.assertNotIn('fingerprint',text);self.assertNotIn('web_token',text);self.assertFalse(chat['operation_replayed']);self.assertEqual(self.http.calls,[])
 def test_restart_restores_uncertainty_without_model_retry(self):
  self.seed(state='running',charge=True);self.w.s.db.execute("INSERT INTO local_memory_requests VALUES ('memory-running-01','fixture','running',NULL)");self.w.close();self.w=Workbench(self.tmp.name,self.http);self.recovery=self.w.local_chat.recovery
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')):pending=self.recovery.pending('default')
  self.assertEqual({(x['kind'],x['state']) for x in pending['items']},{('chat','uncertain'),('memory','uncertain')});self.assertEqual(self.http.calls,[])
 def test_acknowledgement_does_not_reconcile_bill_or_change_original_state(self):
  self.seed(charge=True);before=tuple(self.w.s.db.execute('SELECT * FROM charges').fetchone())
  with self.assertRaises(Fault):self.recovery.acknowledge('default','chat','recover-fixture-01','')
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')):
   result=self.recovery.acknowledge('default','chat','recover-fixture-01','已核对本次对话结果和账单');self.recovery.acknowledge('default','chat','recover-fixture-01','已核对本次对话结果和账单')
  self.assertTrue(result['acknowledged']);self.assertFalse(result['billing_changed']);self.assertEqual(tuple(self.w.s.db.execute('SELECT * FROM charges').fetchone()),before);status=self.recovery.status('default','chat','recover-fixture-01');self.assertEqual(status['state'],'uncertain');self.assertTrue(status['reviewed']);self.assertEqual(self.recovery.pending('default')['items'],[]);self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_recovery_ack').fetchone()[0],1)
 def test_running_or_not_found_cannot_be_acknowledged(self):
  self.seed(state='running')
  for request in ('recover-fixture-01','not-found-fixture-01'):
   with self.assertRaises(Fault):self.recovery.acknowledge('default','chat',request,'已核对本次对话结果和账单')
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_recovery_ack').fetchone()[0],0)
 def test_role_scope_hides_owner_billing_and_memory(self):
  self.seed(charge=True);role=self.w.characters.create('测试角色','测试身份',['认真']);self.w.characters.select(role['id'])
  with self.assertRaises(Fault):self.recovery.pending('default')
  with self.assertRaises(Fault):self.recovery.status(role['id'],'memory','memory-fixture-01')
  result=self.recovery.status(role['id'],'chat','recover-fixture-01');self.assertEqual(result['state'],'not_found');self.assertEqual(result['billing_state'],'not_recorded');self.assertNotIn('billing_request_id',result);self.assertEqual(self.recovery.pending(role['id'])['items'],[])
 def test_bounded_pending_pages_and_query_only_database(self):
  for i in range(24):self.seed(request='pending-fixture-'+str(i))
  first=self.recovery.pending('default');self.assertEqual(len(first['items']),20);self.assertTrue(first['has_more'])
  for item in first['items']:self.recovery.acknowledge('default','chat',item['request_id'],'已核对本次对话结果和账单')
  second=self.recovery.pending('default');self.assertEqual(len(second['items']),4);self.assertFalse(second['has_more'])
  with self.recovery.reader('default') as db:
   with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM local_chat_requests')
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_chat_requests').fetchone()[0],24)
 def test_missing_database_is_not_created_and_invalid_arguments_are_rejected(self):
  missing=Path(self.tmp.name)/'missing'
  with patch.object(self.w.s,'root',missing):
   with self.assertRaises(sqlite3.OperationalError):self.recovery.pending('default')
  self.assertFalse(missing.exists())
  for args in [('..','chat','request-valid-01'),('default','other','request-valid-01'),('default','chat','short')]:
   with self.assertRaises(Fault):self.recovery.status(*args)
 def test_stale_chat_target_is_rejected_before_provider_or_request_insertion(self):
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});role=self.w.characters.create('测试角色','测试身份',['认真']);self.w.characters.select(role['id'])
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')):
   with self.assertRaises(Fault):self.w.local_chat.send('stale-chat-fixture','文字',expected_character='default')
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_chat_requests').fetchone()[0],0)


class LocalRecoveryRouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.servers=[]
 def tearDown(self):
  for server,thread in self.servers:server.shutdown();server.server_close();thread.join()
  self.w.close();self.vault.stop();self.tmp.cleanup()
 def server(self,preview=False):
  server=Server(('127.0.0.1',0),self.w,preview=preview);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.servers.append((server,thread));return server,'http://127.0.0.1:'+str(server.server_port)
 def request(self,server,base,path,payload=None,auth=True):
  headers={'X-Local-Token':server.token} if auth else {};body=None
  if payload is not None:headers['Content-Type']='application/json';body=json.dumps(payload).encode()
  req=urllib.request.Request(base+path,data=body,headers=headers)
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=2) as response:return json.load(response)
  except urllib.error.HTTPError as error:error.read();error.close();raise
 def test_auth_preview_and_reading_during_busy_model(self):
  server,base=self.server()
  with self.assertRaises(urllib.error.HTTPError) as error:self.request(server,base,'/api/local-chat/recovery/default/pending',auth=False)
  self.assertEqual(error.exception.code,403)
  with self.w.s.lock,patch.object(self.w.model,'chat',side_effect=AssertionError('no model')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   self.assertEqual(self.request(server,base,'/api/local-chat/recovery/current')['character_id'],'default');self.assertEqual(self.request(server,base,'/api/local-chat/recovery/default/pending')['items'],[])
  preview,url=self.server(True);self.assertEqual(self.request(preview,url,'/api/local-chat/recovery/default/pending')['items'],[])
  with self.assertRaises(urllib.error.HTTPError) as error:self.request(preview,url,'/api/local-chat/recovery/acknowledge',{'kind':'chat','character_id':'default','request_id':'fixture-valid-01','confirmation':'已核对本次对话结果和账单'})
  self.assertEqual(error.exception.code,403)
 def test_browser_fixture_lost_response_recovers_actual_chat_and_memory_without_retry(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});server,base=self.server()
  with patch.object(self.w.model,'chat',return_value={'text':'模拟恢复回复'}) as model,patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   result=subprocess.run([node,str(Path(__file__).with_name('test_local_recovery_loopback.cjs')),base,server.token],capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('actual outcome recovery routes passed',result.stdout);self.assertEqual(model.call_count,1)
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_chat_requests').fetchone()[0],1);self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_memory_requests').fetchone()[0],1);self.assertEqual(self.http.calls,[])


class LocalRecoveryBrowserTests(unittest.TestCase):
 def test_fixture_browser_outcomes_storage_and_races(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  result=subprocess.run([node,str(Path(__file__).with_name('test_local_recovery.cjs'))],capture_output=True,text=True,timeout=15);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('recovery fixture scenarios passed',result.stdout)


if __name__=='__main__':unittest.main()
