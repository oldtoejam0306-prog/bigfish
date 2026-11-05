import shutil,subprocess,unittest
from pathlib import Path

class DuplexClientTests(unittest.TestCase):
 def test_simulated_browser_transport(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node unavailable')
  root=Path(__file__).parent
  result=subprocess.run([node,str(root/'test_duplex_client.cjs')],capture_output=True,text=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
  self.assertIn('8 simulated scenarios passed',result.stdout)

if __name__=='__main__':unittest.main()
