import base64,json,shutil,subprocess,tempfile,threading,unittest,urllib.error,urllib.request
from pathlib import Path
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.growth import Scope
from bili_workbench.web import Server
from test_stickers import png
from test_workbench import FakeHTTP


class LocalStickerChatTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.encoded=base64.b64encode(png()).decode();self.item=self.w.stickers.add('本机鲸鱼',self.encoded)
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def send(self,request='sticker-fixture-01',character='default'):return self.w.local_chat.send_sticker(request,self.item['id'],character)
 def test_local_image_message_works_with_zero_budget_without_credentials_or_model(self):
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch.object(self.w.model,'chat',side_effect=AssertionError('no model')):
   result=self.send();history=self.w.local_chat.history();image=self.w.local_chat.sticker_image(result['turn_id'],'default')
  self.assertEqual(result['status'],'completed');self.assertFalse(result['model_called']);self.assertFalse(result['platform_sent']);self.assertEqual(len(history['messages']),1)
  message=history['messages'][0];self.assertEqual(message['role'],'user');self.assertEqual(message['content'],'[表情：本机鲸鱼]');self.assertEqual(message['sticker'],dict(self.item,available=True));self.assertNotIn('png_base64',json.dumps(history))
  self.assertEqual(image['png_base64'],self.encoded);self.assertEqual(image['turn_id'],message['id']);self.assertEqual(self.http.calls,[])
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_chat_requests').fetchone()[0],0);self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM charges').fetchone()[0],0);self.assertEqual(self.w.relationship.snapshot()['local_conversations'],0);self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'],[])
 def test_text_markers_and_assistant_output_do_not_become_images(self):
  self.w.growth.record_exchange(Scope.owner(),'local:marker-fixture','[表情：本机鲸鱼]','[表情：本机鲸鱼]','local-owner:本机主人会话')
  self.assertTrue(all('sticker' not in message for message in self.w.local_chat.history()['messages']))
 def test_deduplication_and_request_conflicts_never_duplicate_turns(self):
  first=self.send();second=self.send();self.assertEqual(second['status'],'already_recorded');self.assertEqual(first['turn_id'],second['turn_id']);self.assertEqual(len(self.w.local_chat.history()['messages']),1)
  other=self.w.stickers.add('其他表情',self.encoded)
  with self.assertRaises(Fault):self.w.local_chat.send_sticker('sticker-fixture-01',other['id'],'default')
  self.w.stickers.delete(self.item['id']);self.assertEqual(self.send()['status'],'already_recorded')
 def test_role_switch_rejects_stale_targets_and_keeps_histories_isolated(self):
  owner=self.send();role=self.w.characters.create('角色甲','测试角色',['认真']);self.w.characters.select(role['id'])
  self.assertEqual(self.w.local_chat.history()['messages'],[])
  with self.assertRaises(Fault):self.send(request='sticker-stale-01')
  with self.assertRaises(Fault):self.w.local_chat.sticker_image(owner['turn_id'],'default')
  with self.assertRaises(Fault):self.w.local_chat.sticker_image(owner['turn_id'],role['id'])
  custom=self.send(request='sticker-role-01',character=role['id']);self.assertEqual(len(self.w.local_chat.history()['messages']),1)
  with self.assertRaises(Fault):self.send(character=role['id'])
  self.w.characters.select('default');self.assertEqual(self.w.local_chat.history()['messages'][0]['id'],owner['turn_id'])
  with self.assertRaises(Fault):self.w.local_chat.sticker_image(custom['turn_id'],'default')
 def test_delete_and_same_name_import_cannot_replace_an_old_image(self):
  first=self.send();self.w.stickers.delete(self.item['id']);replacement=self.w.stickers.add('本机鲸鱼',self.encoded)
  self.assertNotEqual(replacement['id'],self.item['id']);message=self.w.local_chat.history()['messages'][0];self.assertFalse(message['sticker']['available']);self.assertEqual(message['sticker']['id'],self.item['id'])
  image=self.w.local_chat.sticker_image(first['turn_id'],'default');self.assertFalse(image['available']);self.assertNotIn('png_base64',image)
 def test_deleted_turn_is_not_exposed_or_restored(self):
  result=self.send()
  with self.w.growth._db() as db:db.execute('UPDATE growth_turns SET deleted=1 WHERE id=?',(result['turn_id'],))
  self.assertEqual(self.w.local_chat.history()['messages'],[])
  with self.assertRaises(Fault):self.w.local_chat.sticker_image(result['turn_id'],'default')
  self.assertEqual(self.send()['status'],'already_recorded');self.assertEqual(self.w.local_chat.history()['messages'],[])
 def test_uncertain_cross_database_write_is_not_replayed(self):
  original=self.w.growth.record_turn
  def committed_then_lost(*args):original(*args);raise OSError('fixture result lost')
  with patch.object(self.w.growth,'record_turn',side_effect=committed_then_lost) as record:
   with self.assertRaises(OSError):self.send()
   result=self.send();self.assertEqual(result['previous_state'],'uncertain');self.assertFalse(result['automatic_retry']);self.assertEqual(record.call_count,1)
  messages=self.w.local_chat.history()['messages'];self.assertEqual(len(messages),1);self.assertNotIn('sticker',messages[0])
 def test_metadata_survives_restart_without_replaying_or_copying_png_to_chat(self):
  result=self.send();self.w.close();self.w=Workbench(self.tmp.name,self.http)
  self.assertEqual(self.w.local_chat.history()['messages'][0]['id'],result['turn_id']);self.assertEqual(self.w.local_chat.sticker_image(result['turn_id'],'default')['png_base64'],self.encoded);self.assertEqual(self.send()['status'],'already_recorded')
  self.assertNotIn(png(),(Path(self.tmp.name)/'growth.db').read_bytes())
 def test_later_model_context_contains_name_only(self):
  self.send();self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  with patch.object(self.w.model,'chat',return_value={'text':'测试回答'}) as model:self.w.local_chat.send('after-sticker-01','你看到了什么文字？')
  history=model.call_args.kwargs['history'];self.assertEqual(history,[{'role':'user','content':'[表情：本机鲸鱼]'}]);self.assertNotIn(self.encoded,str(model.call_args));self.assertNotIn(self.item['id'],str(model.call_args));self.assertEqual(self.http.calls,[])
 def test_actual_model_serializer_only_receives_text_with_fixture_transport(self):
  self.send();self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
  self.w.s.vault.save({'api_key':'fixture-only-sticker-key'});self.w.s.gate=lambda *args,**kwargs:None
  self.w.local_chat.send('sticker-serialized-01','下一句测试')
  calls=[call for call in self.http.calls if call[1].endswith('/chat/completions')];self.assertEqual(len(calls),1);payload=calls[0][3];self.assertIn({'role':'user','content':'[表情：本机鲸鱼]'},payload['messages']);self.assertNotIn(self.encoded,json.dumps(payload));self.assertNotIn(self.item['id'],json.dumps(payload));self.assertNotIn('image_url',json.dumps(payload))
 def test_restart_keeps_unfinished_writes_uncertain_without_replay(self):
  self.w.s.db.execute("INSERT INTO local_sticker_messages VALUES (?,?,?,?,NULL,'running',0)",('sticker-fixture-01',self.item['id'],self.item['name'],'default'));self.w.close();self.w=Workbench(self.tmp.name,self.http)
  with patch.object(self.w.growth,'record_turn',side_effect=AssertionError('no replay')):
   result=self.send();self.assertEqual(result['status'],'already_recorded');self.assertEqual(result['previous_state'],'uncertain')
  self.assertEqual(self.w.local_chat.history()['messages'],[])
 def test_invalid_inputs_are_rejected_before_writing(self):
  for args in [('short',self.item['id'],'default'),('sticker-valid-01','../other','default'),('sticker-valid-01',self.item['id'],'../role'),('sticker-valid-01','f'*24,'default')]:
   with self.subTest(args=args),self.assertRaises(Fault):self.w.local_chat.send_sticker(*args)
  self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM local_sticker_messages').fetchone()[0],0)
  for turn in (True,0,-1,9007199254740992):
   with self.assertRaises(Fault):self.w.local_chat.sticker_image(turn,'default')


class LocalStickerRouteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.item=self.w.stickers.add('本机示例',base64.b64encode(png()).decode());self.servers=[]
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
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5) as response:return json.load(response)
  except urllib.error.HTTPError as error:error.read();error.close();raise
 def test_authentication_preview_and_scoped_images(self):
  payload={'request_id':'http-sticker-01','sticker_id':self.item['id'],'character_id':'default'}
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   for preview,auth in ((False,False),(True,True)):
    server,base=self.server(preview)
    with self.assertRaises(urllib.error.HTTPError) as error:self.request(server,base,'/api/local-chat/sticker',payload,auth)
    self.assertEqual(error.exception.code,403)
   server,base=self.server();result=self.request(server,base,'/api/local-chat/sticker',payload);path='/api/local-chat/sticker-image/default/'+str(result['turn_id'])
   self.assertTrue(self.request(server,base,path)['available'])
   with self.assertRaises(urllib.error.HTTPError) as error:self.request(server,base,path,auth=False)
   self.assertEqual(error.exception.code,403)
   with self.assertRaises(urllib.error.HTTPError) as error:self.request(server,base,'/api/local-chat/sticker-image/default/0')
   self.assertEqual(error.exception.code,400)
  self.assertEqual(self.http.calls,[])
 def test_browser_module_to_actual_http_storage_and_image_read(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  server,base=self.server()
  with patch.object(self.w.model,'chat',side_effect=AssertionError('no model')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')):
   result=subprocess.run([node,str(Path(__file__).with_name('test_sticker_chat_loopback.cjs')),base,server.token,self.item['id']],capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('actual local sticker routes passed',result.stdout)
  self.assertEqual(self.http.calls,[]);self.assertEqual(self.w.s.db.execute('SELECT COUNT(*) FROM charges').fetchone()[0],0)


class StickerBrowserTests(unittest.TestCase):
 def test_sticker_module_with_fixture_dom(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  result=subprocess.run([node,str(Path(__file__).with_name('test_sticker_chat.cjs'))],capture_output=True,text=True,timeout=15);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('sticker fixture scenarios passed',result.stdout)


if __name__=='__main__':unittest.main()
