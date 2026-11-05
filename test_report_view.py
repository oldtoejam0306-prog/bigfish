import json,re,shutil,subprocess,tempfile,threading,time,unittest,urllib.error,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from bili_workbench.runtime import Vault
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP


class ReportViewTests(unittest.TestCase):
 def test_browser_report_states_with_fixture_dom(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  root=Path(__file__).parent;result=subprocess.run([node,str(root/'test_report_view.cjs')],capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('8 simulated scenarios passed',result.stdout)
 def test_all_inline_scripts_and_report_asset_parse_without_execution(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  root=Path(__file__).parent/'bili_workbench';html=(root/'index.html').read_text(encoding='utf8');code='\n;\n'.join(re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',html,re.S))
  for script in (code,(root/'report_view.js').read_text(encoding='utf8')):
   result=subprocess.run([node,'--check'],input=script,capture_output=True,text=True,timeout=15);self.assertEqual(result.returncode,0,result.stderr)


class ReportHttpTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.server=Server(('127.0.0.1',0),self.w);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
  now=time.time();day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
  self.w.s.db.executemany('INSERT INTO local_chat_requests VALUES (?,?,?,?)',[('report-1','fixture','completed',now),('report-2','fixture','completed',now),('report-3','fixture','blocked',now)])
  self.w.s.db.execute('INSERT INTO manual_video_reads VALUES (?,?,?,?)',('anonymous','BV1q2nW6DEaf','subtitle',now));self.w.s.db.execute('INSERT INTO computer_activity_daily VALUES (?,?,?)',(day,'工作',59))
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.w.close();self.vault.stop();self.tmp.cleanup()
 def get(self,path,auth=True):
  req=urllib.request.Request(self.base+path,headers={'X-Local-Token':self.server.token} if auth else {})
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=3) as response:return json.load(response)
  except urllib.error.HTTPError as error:error.read();error.close();raise
 def test_actual_report_gets_do_not_start_sampling_read_credentials_or_call_model(self):
  with patch.object(self.w.system_activity,'idle',side_effect=AssertionError('no idle capture')),patch.object(self.w.system_activity,'foreground',side_effect=AssertionError('no process query')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch.object(self.w.model,'chat') as model:
   local=self.get('/api/activity-report/day');system=self.get('/api/system-activity/day');status=self.get('/api/system-activity/status')
   self.assertEqual(local['local_chat_completed'],2);self.assertEqual(local['bili_manual_read_records'],1);self.assertEqual(system['categories'],[{'category':'工作','seconds':59}]);self.assertFalse(status['running']);model.assert_not_called();self.assertEqual(self.http.calls,[])
 def test_unauthenticated_reports_are_not_exposed(self):
  for path in ('/api/activity-report/day','/api/system-activity/day','/api/system-activity/status'):
   with self.assertRaises(urllib.error.HTTPError) as error:self.get(path,auth=False)
   self.assertEqual(error.exception.code,403)
 def test_rendered_report_module_uses_actual_local_routes_with_fixture_dom(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  with patch.object(self.w.system_activity,'idle',side_effect=AssertionError('no idle capture')),patch.object(self.w.system_activity,'foreground',side_effect=AssertionError('no process query')),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')),patch.object(self.w.model,'chat') as model:
   script=Path(__file__).with_name('test_report_loopback.cjs');result=subprocess.run([node,str(script),self.base,self.server.token],capture_output=True,text=True,timeout=15)
   self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);self.assertIn('actual local GET endpoints passed',result.stdout);self.assertFalse(self.w.system_activity.running);model.assert_not_called();self.assertEqual(self.http.calls,[])


if __name__=='__main__':unittest.main()
