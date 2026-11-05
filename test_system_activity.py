import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.system_activity import SystemActivity,windows_idle_seconds
from bili_workbench.growth import Scope
from test_workbench import FakeHTTP

class SystemActivityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_default_off_and_explicit_consent(self):
  calls=[];s=SystemActivity(self.w,idle=lambda:calls.append(1),foreground=lambda:'工作')
  s.tick(now=100000,monotonic=0);self.assertEqual(calls,[]);self.assertFalse(s.status()['running'])
  with self.assertRaises(Fault):s.start(True,False,False,300,900,3,'','',start_thread=False)
  with self.assertRaises(Fault):s.start(False,True,True,300,900,3,'','',start_thread=False)
  self.assertEqual(calls,[])
 def test_hot_reload_is_blocked_while_system_monitor_runs(self):
  from bili_workbench.hot_reload import blocking_reason
  self.w.system_activity.start(False,True,False,300,900,3,'','',start_thread=False)
  self.assertIn('电脑活动监测',blocking_reason(self.w))
  self.w.system_activity.stop();self.assertIsNone(blocking_reason(self.w))
 def test_idle_return_cooldown_and_coarse_report(self):
  values=iter((290,305,320,0,300,315,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'secret-app-name')
  s.start(True,True,False,300,900,3,'确认记录电脑活动分类','',start_thread=False)
  for t in (0,15,30,45,345,360,375):s.tick(now=100000+t,monotonic=t)
  self.assertEqual(len(s.status()['events']),1)
  report=s.report('day',now=100375);self.assertEqual(report['categories'][0]['category'],'无法识别');self.assertEqual(report['categories'][0]['seconds'],30)
  self.assertNotIn('secret-app-name',str(report))
  with self.assertRaises(Fault):s.clear_report('')
  s.stop();s.clear_report('确认删除电脑活动统计');self.assertEqual(s.report('day',now=100060)['categories'],[])
  self.assertEqual(self.http.calls,[])
 def test_opted_in_model_runs_once_without_memory_or_platform_send(self):
  values=iter((500,515,0,300,315,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',start_thread=False)
  with patch.object(self.w.model,'chat',return_value={'text':'模拟欢迎'}) as model:
   for t in (0,15,30,330,345,360):s.tick(now=100000+t,monotonic=t)
   self.assertEqual(model.call_count,1);self.assertEqual(model.call_args.args[3],[]);self.assertTrue(model.call_args.kwargs['private_uncached'])
  self.assertEqual(s.status()['events'][0]['text'],'模拟欢迎');self.assertEqual(self.http.calls,[]);s.stop()
 def test_budget_zero_pauses_auto_model_without_call(self):
  values=iter((500,515,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',start_thread=False)
  with patch.object(self.w.model,'chat') as model:
   s.tick(now=100000,monotonic=0);s.tick(now=100015,monotonic=15);s.tick(now=100030,monotonic=30);model.assert_not_called()
  self.assertFalse(s.status()['options']['model_reply'])
  self.assertEqual(self.w.s.db.execute('SELECT state FROM proactive_attempts').fetchone()[0],'blocked_not_sent');s.stop()
 def test_windows_tick_wrap_with_mock_native_functions(self):
  class Func:
   def __init__(self,fn):self.fn=fn
   def __call__(self,*args):return self.fn(*args)
  class Obj:pass
  user=Obj();kernel=Obj()
  def last(ptr):ptr._obj.dwTime=0xfffffffa;return 1
  user.GetLastInputInfo=Func(last);kernel.GetTickCount64=Func(lambda:0x100000005)
  with patch('bili_workbench.system_activity.ctypes.WinDLL',side_effect=lambda name,**kw:user if name=='user32' else kernel):
   self.assertEqual(windows_idle_seconds(),11)
 def test_offpeak_deferral_never_calls_model(self):
  from bili_workbench.deepseek_test import OffPeakDeferred
  values=iter((500,515,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',start_thread=False)
  with patch('bili_workbench.deepseek_test.require_window',side_effect=OffPeakDeferred({})),patch.object(self.w.model,'chat') as model:
   s.tick(now=100000,monotonic=0);s.tick(now=100015,monotonic=15);s.tick(now=100030,monotonic=30);model.assert_not_called()
  self.assertEqual(self.w.s.db.execute('SELECT state FROM proactive_attempts').fetchone()[0],'deferred_not_sent');s.stop()
 def test_fixture_proactive_model_path_is_private_and_uncached(self):
  values=iter((500,515,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY'})
  self.w.s.vault.save({'api_key':'fixture-only-secret'});self.w.s.gate=lambda *args,**kwargs:None
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',start_thread=False)
  s.tick(now=100000,monotonic=0);s.tick(now=100015,monotonic=15);s.tick(now=100030,monotonic=30)
  calls=[x for x in self.http.calls if x[1].endswith('/chat/completions')]
  self.assertEqual(len(calls),1);self.assertEqual(self.w.s.db.execute('SELECT count(*) FROM cache').fetchone()[0],0)
  self.assertIn('不要猜测离开期间',calls[0][3]['messages'][-1]['content']);s.stop()
 def test_idle_jump_does_not_trigger_welcome(self):
  values=iter((0,10000,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  s.start(False,True,False,300,900,3,'','',start_thread=False)
  for i in range(3):s.tick(now=100000+i*15,monotonic=i*15)
  self.assertEqual(s.status()['events'],[]);s.stop()
 def test_opted_in_context_is_scoped_and_requires_confirmation(self):
  values=iter((500,515,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  self.w.growth.controls().remember(Scope.owner(),'主人明确提供的测试记忆','fixture:本人确认',0.9)
  self.w.growth.record_exchange(Scope.owner(),'fixture-history','测试提问','测试回答','local-owner:fixture')
  with self.assertRaises(Fault):s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',include_memory=True,start_thread=False)
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',include_memory=True,include_history=True,context_confirmation='确认将所选私人上下文发送给模型',start_thread=False)
  with patch.object(self.w.model,'chat',return_value={'text':'模拟问候'}) as model:
   s.tick(now=100000,monotonic=0);s.tick(now=100015,monotonic=15);s.tick(now=100030,monotonic=30)
   self.assertEqual(len(model.call_args.args[3]),1);self.assertEqual(len(model.call_args.kwargs['history']),2)
  event=s.status()['events'][-1];draft=s.reply_draft(event['id'])
  self.assertIn('模拟问候',draft['text']);self.assertFalse(draft['automatically_sent']);self.assertFalse(draft['saved_as_memory'])
  role=self.w.characters.create('其他角色','测试身份',['测试']);self.w.characters.select(role['id'])
  with self.assertRaises(Fault):s.reply_draft(event['id'])
  s.stop();self.assertEqual(self.http.calls,[])
 def test_switching_roles_prevents_default_private_memory_leak(self):
  values=iter((500,515,0));s=SystemActivity(self.w,idle=lambda:next(values),foreground=lambda:'其他')
  self.w.s.configure({'paid_enabled':True,'daily_budget_micro':1000,'request_cap_micro':100})
  s.start(False,True,True,300,900,3,'','确认按预算自动调用模型',include_memory=True,context_confirmation='确认将所选私人上下文发送给模型',start_thread=False)
  role=self.w.characters.create('其他角色','测试身份',['测试']);self.w.characters.select(role['id'])
  with patch.object(self.w.model,'chat') as model:
   s.tick(now=100000,monotonic=0);s.tick(now=100015,monotonic=15);s.tick(now=100030,monotonic=30);model.assert_not_called()
  self.assertFalse(s.status()['options']['model_reply']);s.stop()

if __name__=='__main__':unittest.main()
