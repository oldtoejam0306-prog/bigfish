import os,subprocess,tempfile,unittest
from pathlib import Path

class IsolatedLauncherTests(unittest.TestCase):
 def test_candidate_launcher_uses_separate_default_data_directory(self):
  script=Path('start-bili.cmd').read_text(encoding='utf8')
  self.assertIn('BIGFISH_PERSONA_DATA_DIR',script)
  self.assertIn('WhalePersona-20261105-simple-settings',script)
  self.assertIn('--port 0',script)
  self.assertNotIn('BigFish\\BiliWorkbench',script)

 @unittest.skipUnless(os.name=='nt','Windows cmd fixture only')
 def test_requested_reload_reuses_exact_data_directory_with_fixture_child(self):
  result,calls=self.run_fixture([75,0]);self.assertEqual(result.returncode,0);self.assertEqual(len(calls),2);self.assertEqual(calls[0],calls[1]);self.assertIn('--supervised',calls[0]);self.assertIn('--port 0',calls[0]);self.assertIn('fixture data',calls[0])

 @unittest.skipUnless(os.name=='nt','Windows cmd fixture only')
 def test_normal_stop_and_failure_do_not_restart_fixture_child(self):
  for code in (0,37):
   with self.subTest(code=code):
    result,calls=self.run_fixture([code]);self.assertEqual(result.returncode,code);self.assertEqual(len(calls),1)

 def run_fixture(self,codes):
  temp_root=Path(tempfile.gettempdir()).resolve()
  with tempfile.TemporaryDirectory(prefix='bigfish-launcher-fixture-',dir=temp_root) as folder:
   root=Path(folder).resolve();self.assertEqual(root.parent,temp_root);self.assertTrue(root.name.startswith('bigfish-launcher-fixture-'))
   (root/'start-bili.cmd').write_bytes(Path(__file__).with_name('start-bili.cmd').read_bytes())
   lines=['@echo off','if exist "%~dp0first.marker" goto second','>"%~dp0first.marker" echo first','>"%~dp0args1.txt" echo %*','exit /b '+str(codes[0]),':second','>"%~dp0second.marker" echo second','>"%~dp0args2.txt" echo %*','exit /b '+str(codes[-1])]
   (root/'python-run.cmd').write_bytes(('\r\n'.join(lines)+'\r\n').encode('utf8'))
   env=dict(os.environ,BIGFISH_PERSONA_DATA_DIR=str(root/'fixture data'))
   result=subprocess.run([os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/q','/c','start-bili.cmd'],cwd=root,env=env,input='\n',capture_output=True,text=True,encoding='utf8',errors='replace',timeout=10)
   calls=[p.read_text(encoding='utf8',errors='replace').strip() for p in sorted(root.glob('args?.txt'))]
   return result,calls

if __name__=='__main__':unittest.main()
