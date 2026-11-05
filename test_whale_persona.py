import base64,hashlib,hmac,io,json,shutil,subprocess,tempfile,threading,unittest,urllib.request,wave
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from bigfish_persona import INITIAL_PERSONALITY,PERSONA_PROMPT,format_reply,chat_system,night_key
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.owner_expression import mood,validate_binary
from bili_workbench.qq_transport import OneBotHTTP,TransportConfig
from core import Core
from test_stickers import png
from test_workbench import FakeHTTP
from test_qq_transport import MockHTTP
from bili_workbench.web import Server
from pathlib import Path

def wav():
 stream=io.BytesIO()
 with wave.open(stream,'wb') as value:value.setnchannels(1);value.setsampwidth(2);value.setframerate(16000);value.writeframes(b'\0\0'*8000)
 return stream.getvalue()

class WhalePersonaTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.protection=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.protection.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
 def tearDown(self):self.w.close();self.protection.stop();self.tmp.cleanup()
 def model(self):
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'});self.w.s.vault.save({'api_key':'fixture-persona-only-key'});self.w.s.gate=lambda *args,**kwargs:None
 def test_new_default_and_existing_personality_are_not_overwritten(self):
  core=Core(self.w.s.root/'character.db')
  try:
   self.assertEqual(core.personality()['data'],INITIAL_PERSONALITY);core.correct('主人确认兴趣',dict(INITIAL_PERSONALITY,creative_interest='绘画'));before=core.personality()
  finally:core.close()
  core=Core(self.w.s.root/'character.db')
  try:self.assertEqual(core.personality(),before)
  finally:core.close()
 def test_every_turn_real_serialization_loads_whale_rules_without_owner_number(self):
  self.model();self.w.qq.configure({'bot_id':10001,'owner_id':20002})
  self.w.local_chat.send('whale-first-01','在吗');self.w.local_chat.send('whale-second-01','今天忙什么')
  calls=[call for call in self.http.calls if call[1].endswith('/chat/completions')];self.assertEqual(len(calls),2)
  for call in calls:
   system=call[3]['messages'][0]['content']
   for value in ('蓝色小鲸鱼','token','嘴硬心软','30字','没有真实回执','QQ号'):self.assertIn(value,system)
   self.assertNotIn('20002',system);self.assertNotIn('养成型虚拟女儿',system)
 def test_short_input_budget_has_compact_persona_and_no_automatic_budget_increase(self):
  self.model();self.w.s.configure({'max_input_tokens':2048});before=self.w.s.config()
  self.w.model.chat('在吗','whale-small-01',{},[],conversation={'audience':'owner_private'})
  prompt=self.http.calls[0][3]['messages'][0]['content'];self.assertIn('嘴硬心软',prompt);self.assertLess(len(prompt.encode())+256,2048);self.assertEqual(before,self.w.s.config())
  with self.assertRaises(Fault):self.w.model.chat('很长的输入'*500,'whale-large-01',{},[],conversation={'audience':'owner_private'})
  self.assertEqual(len(self.http.calls),1)
 def test_custom_role_uses_its_own_identity_without_whale_role_injection(self):
  system=chat_system('测试自定义JSON',{'character_kind':'custom'},16000);self.assertIn('选择的虚拟角色',system);self.assertNotIn('token是饭',system);self.assertNotIn('蓝色小鲸鱼',system)
 def test_line_formatting_keeps_content_math_and_single_emoji(self):
  source='## 今天\n- **太好了**。我就说你行。🐋😎\n'+('认真帮你把这件事情讲清楚'*5)+'\n2**3和__init__，版本1.2'
  value=format_reply(source);self.assertNotIn('。',value);self.assertNotIn('##',value);self.assertNotIn('**太好了**',value);self.assertIn('2**3',value);self.assertIn('__init__',value);self.assertIn('1.2',value);self.assertEqual(value.count('🐋'),1);self.assertNotIn('😎',value);self.assertTrue(all(len(line)<=30 for line in value.splitlines()))
  self.assertEqual(format_reply('🇨🇳😂'),'🇨🇳');self.assertEqual(format_reply(format_reply(source)),value)
 def test_owner_identity_is_hidden_from_public_context_and_public_response(self):
  self.model();self.w.qq.configure({'bot_id':10001,'owner_id':20002})
  with self.assertRaises(Fault):self.w.model.chat('主人的QQ是20002','privacy-input-01',{},[],conversation={'audience':'public'})
  self.assertEqual(self.http.calls,[])
  original=self.http.json
  def response(*args,**kwargs):
   result=original(*args,**kwargs)
   if args[1].endswith('/chat/completions'):result['choices'][0]['message']['content']='主人QQ是20002'
   return result
  self.http.json=response
  with self.assertRaises(Fault):self.w.model.chat('说说主人','privacy-output-01',{},[],conversation={'audience':'public'})
  self.assertEqual(self.w.s.db.execute("SELECT COUNT(*) FROM cache WHERE key LIKE 'model:%'").fetchone()[0],0)
 def test_local_assistant_uses_saved_sticker_every_four_appropriate_replies(self):
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});item=self.w.stickers.add('开心鲸鱼',base64.b64encode(png()).decode())
  with patch.object(self.w.model,'chat',return_value={'text':'才没有。你再夸一句。'}) as model:
   for i in range(4):result=self.w.local_chat.send('sticker-turn-'+str(i),'谢谢，你真可爱')
  self.assertEqual(model.call_count,4);self.assertEqual(result['expression']['sticker']['id'],item['id']);self.assertFalse(result['expression']['voice_due']);self.assertEqual(self.http.calls,[])
  messages=self.w.local_chat.history()['messages'];reply=[message for message in messages if message['role']=='assistant'][-1];self.assertEqual(reply['expression']['sticker']['id'],item['id']);image=self.w.local_chat.sticker_image(reply['id'],'default');self.assertTrue(image['available']);self.assertNotIn('png_base64',json.dumps(messages))
  self.w.stickers.delete(item['id']);self.w.stickers.add('开心鲸鱼',base64.b64encode(png()).decode());self.assertFalse(self.w.local_chat.sticker_image(reply['id'],'default')['available'])
 def test_public_or_custom_role_never_receives_owner_expression(self):
  plan=self.w.owner_expression.plan('qq','public-plan-01','我面试过了','真棒',owner_private=False);self.assertFalse(plan['eligible']);self.assertFalse(plan['voice_due']);self.assertIsNone(plan['sticker'])
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});role=self.w.characters.create('别的角色','测试身份',['测试语气']);self.w.characters.select(role['id'])
  with patch.object(self.w.model,'chat',return_value={'text':'原有语气。'}):result=self.w.local_chat.send('custom-plan-01','面试过了')
  self.assertFalse(result['expression']['eligible']);self.assertEqual(result['reply'],'原有语气。')
 def test_emotional_voice_falls_back_without_permission_and_without_model_retry(self):
  self.assertEqual(mood('我面试没过','下次再来')[0],'comfort');self.assertEqual(mood('我面试过了','好耶'),('happy',True))
  plan=self.w.owner_expression.plan('local','emotion-first-01','我面试过了','我就说你行',owner_private=True);self.assertTrue(plan['voice_due'])
  with patch('bili_workbench.system_voice.synthesize',side_effect=AssertionError('no unapproved speech')) as speech:
   segments,status=self.w.owner_expression.segments('emotion-first-01','我就说你行',voice_renderer=speech);speech.assert_not_called()
  self.assertEqual([part['type'] for part in segments],['text']);self.assertFalse(status['voice_prepared']);self.assertEqual(self.http.calls,[])
  self.w.owner_expression.configure({'native_voice_authorized':True});calls=[]
  def fail(text):calls.append(text);raise RuntimeError('fixture failure')
  segments,status=self.w.owner_expression.segments('emotion-first-01','我就说你行',voice_renderer=fail);self.assertEqual(len(calls),1);self.assertEqual(len(segments),1);self.assertTrue(status['voice_unavailable'])
  segments,status=self.w.owner_expression.segments('emotion-first-01','我就说你行',voice_renderer=lambda text:wav());self.assertTrue(status['voice_prepared']);self.assertEqual(segments[1]['type'],'record')
 def test_voice_normalizes_existing_native_pcm_rates_without_external_tools(self):
  self.w.owner_expression.configure({'native_voice_authorized':True});self.w.owner_expression.plan('local','voice-normalize-01','面试过了','好耶',owner_private=True);stream=io.BytesIO()
  with wave.open(stream,'wb') as output:output.setnchannels(2);output.setsampwidth(2);output.setframerate(22050);output.writeframes(b'\0\0\0\0'*11025)
  with patch('subprocess.Popen',side_effect=AssertionError('no external converter')):
   segments,status=self.w.owner_expression.segments('voice-normalize-01','好耶',voice_renderer=lambda text:stream.getvalue())
  self.assertTrue(status['voice_prepared']);raw=validate_binary('record',segments[1]['data']['file'][9:])
  with wave.open(io.BytesIO(raw),'rb') as sound:self.assertEqual((sound.getnchannels(),sound.getframerate(),sound.getnframes()),(1,16000,8000))
 def test_sleep_nudge_once_across_midnight_and_idempotent_commits(self):
  tz=timezone(timedelta(hours=8));now=datetime(2026,11,5,23,30,tzinfo=tz);self.w.owner_expression.clock=lambda:now
  first=self.w.owner_expression.plan('local','night-first-01','在吗','早点睡\n我饿了',owner_private=True);self.w.owner_expression.commit('night-first-01','早点睡\n我饿了');self.w.owner_expression.commit('night-first-01','早点睡\n我饿了')
  self.assertFalse(self.w.owner_expression.night_allowed());self.assertEqual(self.w.owner_expression.filter_sleep('早点睡\n我饿了'),'我饿了');self.assertEqual(self.w.s.db.execute('SELECT total FROM owner_expression_counts').fetchone()[0],1)
  now+=timedelta(hours=1);self.assertEqual(night_key(now),'2026-11-05');self.assertFalse(self.w.owner_expression.night_allowed());now+=timedelta(hours=23);self.assertTrue(self.w.owner_expression.night_allowed())
 def test_assistant_sticker_formal_browser_module_to_actual_local_http(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100});self.w.stickers.add('开心鲸鱼',base64.b64encode(png()).decode());server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with patch.object(self.w.model,'chat',return_value={'text':'才没有。再夸一句。'}) as model,patch('bili_workbench.system_voice.synthesize',side_effect=AssertionError('no native voice')):
    result=subprocess.run([node,str(Path(__file__).with_name('test_whale_expression_loopback.cjs')),'http://127.0.0.1:'+str(server.server_port),server.token],capture_output=True,text=True,timeout=20);self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertEqual(model.call_count,4);self.assertIn('assistant sticker HTTP fixture passed',result.stdout)
  finally:server.shutdown();server.server_close();thread.join()
 def test_readonly_preview_does_not_probe_speech_or_execute_native_scripts(self):
  server=Server(('127.0.0.1',0),self.w,preview=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   request=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/api/voice/readiness',headers={'X-Local-Token':server.token})
   with patch('bili_workbench.voice_input.readiness',side_effect=AssertionError('no native probe')) as probe:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:data=json.load(response)
    probe.assert_not_called();self.assertFalse(data['ready']);self.assertFalse(data['checked'])
  finally:server.shutdown();server.server_close();thread.join()
 def test_preferences_http_save_does_not_grant_media_or_native_permissions(self):
  server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));url='http://127.0.0.1:'+str(server.server_port)+'/api/owner-expression'
  try:
   request=urllib.request.Request(url,data=json.dumps({'preferences':{'stickers':False,'emotional_voice':True}}).encode(),headers={'X-Local-Token':server.token,'Content-Type':'application/json'})
   with patch('bili_workbench.system_voice.synthesize',side_effect=AssertionError('preferences must not synthesize')) as native:
    with opener.open(request,timeout=3) as response:data=json.load(response)
    native.assert_not_called();self.assertFalse(data['stickers']);self.assertTrue(data['emotional_voice']);self.assertFalse(data['qq_media_enabled']);self.assertFalse(data['qq_media_validated']);self.assertFalse(data['native_voice_authorized'])
   request=urllib.request.Request(url,data=json.dumps({'preferences':{'native_voice_authorized':True}}).encode(),headers={'X-Local-Token':server.token,'Content-Type':'application/json'})
   with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(request,timeout=3)
   caught.exception.close();self.assertFalse(self.w.owner_expression.settings()['native_voice_authorized']);self.assertEqual(self.http.calls,[])
  finally:server.shutdown();server.server_close();thread.join()

class WhaleOwnerTransportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.protection=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.protection.start();self.w=Workbench(self.tmp.name,FakeHTTP());self.w.s.gate=lambda *args,**kwargs:None;self.http=MockHTTP()
 def tearDown(self):self.w.close();self.protection.stop();self.tmp.cleanup()
 def transport(self,**options):
  config=TransportConfig(endpoint='http://127.0.0.1:5700',bot_id=10001,owner_id=20002,access_token='fixture-access-token',event_secret='fixture-event-secret',enabled=True,transport_validated=True,owner_media_enabled=True,media_validated=True,**options)
  return OneBotHTTP(self.w.s,config,http=self.http,clock=lambda:1000000,permission=lambda kind,peer:True)
 def candidate(self,segments,user=20002,echo='owner-media-01'):return {'action':'send_private_msg','params':{'user_id':user,'message':segments},'echo':echo}
 def test_known_binary_images_and_voice_prepare_and_send_to_fixture_once(self):
  transport=self.transport();image=base64.b64encode(png()).decode();sound=base64.b64encode(wav()).decode();segments=[{'type':'text','data':{'text':'我就说你行'}},{'type':'image','data':{'file':'base64://'+image}},{'type':'record','data':{'file':'base64://'+sound}}]
  draft=transport.prepare(self.candidate(segments),local_authorization=True);self.assertEqual(self.http.calls,[]);self.assertEqual([part['type'] for part in transport.inspect(draft['id'])['params']['message']],['text','image','record']);transport.send(draft['id'],'确认发送');count=len(self.http.calls)
  with self.assertRaises(Fault):transport.send(draft['id'],'确认发送')
  self.assertEqual(len(self.http.calls),count)
 def test_media_not_allowed_for_others_groups_unvalidated_or_arbitrary_paths(self):
  transport=self.transport();image={'type':'image','data':{'file':'base64://'+base64.b64encode(png()).decode()}};text={'type':'text','data':{'text':'在'}}
  with self.assertRaises(Fault):transport.prepare(self.candidate([text,image],user=30003),local_authorization=True)
  group={'action':'send_group_msg','params':{'group_id':40004,'message':[text,image]},'echo':'group-media-01'}
  with self.assertRaises(Fault):transport.prepare(group,local_authorization=True)
  from dataclasses import replace
  transport.c=replace(transport.c,media_validated=False)
  with self.assertRaises(Fault):transport.prepare(self.candidate([text,image]),local_authorization=True)
  transport.c=replace(transport.c,media_validated=True)
  for value in ['file:///private/secret','http://example.test/pic','base64://YmFk']:
   with self.assertRaises(Fault):transport.prepare(self.candidate([text,{'type':'image','data':{'file':value}}]),local_authorization=True)
  self.assertEqual(self.http.calls,[])
 def test_unknown_media_receipt_never_triggers_text_fallback_send(self):
  transport=self.transport();segments=[{'type':'text','data':{'text':'在'}},{'type':'record','data':{'file':'base64://'+base64.b64encode(wav()).decode()}}];draft=transport.prepare(self.candidate(segments),local_authorization=True);self.http.receipt={'status':'async','retcode':1,'data':None}
  with self.assertRaises(Fault):transport.send(draft['id'],'确认发送')
  count=len(self.http.calls);self.assertEqual(transport.inspect(draft['id'])['state'],'uncertain')
  with self.assertRaises(Fault):transport.send(draft['id'],'确认发送')
  self.assertEqual(len(self.http.calls),count)
 def test_permission_revocation_prevents_media_before_any_network(self):
  transport=self.transport();image=base64.b64encode(png()).decode();draft=transport.prepare(self.candidate([{'type':'text','data':{'text':'在'}},{'type':'image','data':{'file':'base64://'+image}}]),local_authorization=True)
  from dataclasses import replace
  transport.c=replace(transport.c,owner_media_enabled=False)
  with self.assertRaises(Fault):transport.send(draft['id'],'确认发送')
  self.assertEqual(self.http.calls,[])
 def test_authenticated_workbench_reply_path_attaches_sticker_and_records_only_text(self):
  self.w.qq.configure({'bot_id':10001,'owner_id':20002,'enabled':True,'per_peer_daily_limit':20});self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY','qq_endpoint':'http://127.0.0.1:5700','qq_sender_enabled':True,'qq_transport_validated':True});self.w.s.vault.save({'api_key':'fixture-persona-key','qq_access_token':'fixture-access-token','qq_event_secret':'fixture-event-secret'});self.w.owner_expression.configure({'qq_media_enabled':True,'qq_media_validated':True});self.w.stickers.add('开心鲸鱼',base64.b64encode(png()).decode());transport=self.w.qq_transport();transport.http=self.http;transport.clock=lambda:1000000
  with patch.object(self.w.model,'chat',return_value={'text':'才没有。再夸一句。'}) as model:
   for index in range(4):
    event={'time':1000000,'self_id':10001,'post_type':'message','message_type':'private','sub_type':'friend','message_id':index+1,'user_id':20002,'message':[{'type':'text','data':{'text':'谢谢，你真可爱'}}]};raw=json.dumps(event,ensure_ascii=False,separators=(',',':')).encode();headers={'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(b'fixture-event-secret',raw,hashlib.sha1).hexdigest()};self.w.accept_qq_event(raw,headers);result=self.w.process_qq_inbox(1)['results'][0];self.assertEqual(result['status'],'model_draft');draft=result['transport_draft'];again=self.w.prepare_qq_reply(result['event_key'],'确认准备QQ回复');self.assertEqual(again['id'],draft['id']);self.w.send_qq(draft['id'],'确认发送')
   self.assertEqual(model.call_count,4)
  sends=[call for call in self.http.calls if call[1].endswith('/send_private_msg')];self.assertEqual(len(sends),4);self.assertEqual([part['type'] for part in sends[-1][3]['message']],['text','image']);self.assertTrue(all([part['type'] for part in call[3]['message']]==['text'] for call in sends[:-1]));self.assertEqual(self.w.s.db.execute("SELECT total FROM owner_expression_counts WHERE channel='qq'").fetchone()[0],4)
  from bili_workbench.growth import Scope
  history=self.w.growth.context(Scope.owner(),source_prefix='qq-authenticated:')['messages'];self.assertNotIn('base64',json.dumps(history));self.assertNotIn('png_base64',json.dumps(history))
 def test_voice_is_synthesized_once_when_reopening_prepared_owner_draft(self):
  self.w.qq.configure({'bot_id':10001,'owner_id':20002,'enabled':True});self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY','qq_endpoint':'http://127.0.0.1:5700','qq_sender_enabled':True,'qq_transport_validated':True});self.w.s.vault.save({'api_key':'fixture-persona-key','qq_access_token':'fixture-access-token','qq_event_secret':'fixture-event-secret'});self.w.owner_expression.configure({'qq_media_enabled':True,'qq_media_validated':True,'native_voice_authorized':True});transport=self.w.qq_transport();transport.http=self.http;transport.clock=lambda:1000000;rendered=[]
  def render(text):rendered.append(text);return wav()
  self.w.owner_voice_renderer=render;event={'time':1000000,'self_id':10001,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'user_id':20002,'message':[{'type':'text','data':{'text':'我面试过了'}}]};raw=json.dumps(event,ensure_ascii=False,separators=(',',':')).encode();headers={'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(b'fixture-event-secret',raw,hashlib.sha1).hexdigest()}
  with patch.object(self.w.model,'chat',return_value={'text':'我就说你行'}) as model:
   self.w.accept_qq_event(raw,headers);result=self.w.process_qq_inbox(1)['results'][0];self.assertEqual(result['status'],'model_draft');draft=result['transport_draft'];self.assertTrue(draft['expression']['voice_prepared']);self.w.prepare_qq_reply(result['event_key'],'确认准备QQ回复');self.assertEqual(len(rendered),1);self.assertEqual(model.call_count,1);self.w.send_qq(draft['id'],'确认发送')
  sent=[call for call in self.http.calls if call[1].endswith('/send_private_msg')];self.assertEqual(len(sent),1);self.assertEqual([part['type'] for part in sent[0][3]['message']],['text','record'])

if __name__=='__main__':unittest.main()
