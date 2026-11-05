import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from bili_workbench.computer_actions import ComputerActions
from test_workbench import FakeHTTP

class ComputerActionTests(unittest.TestCase):
 def test_allowlist_confirmation_expiry_and_no_retry(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   w=Workbench(root,FakeHTTP());calls=[]
   try:
    a=ComputerActions(w.s,launcher=lambda p:calls.append(p),path_resolver=lambda f:'fixture/'+f)
    with self.assertRaises(Fault):a.prepare('shell')
    d=a.prepare('notepad',now=1000)
    with self.assertRaises(Fault):a.execute(d['id'],'',now=1001)
    self.assertEqual(calls,[])
    result=a.execute(d['id'],d['confirmation'],now=1002);self.assertEqual(result['state'],'launched');self.assertEqual(calls,['fixture/notepad.exe'])
    with self.assertRaises(Fault):a.execute(d['id'],d['confirmation'],now=1003)
    expired=a.prepare('calculator',now=2000)
    with self.assertRaises(Fault):a.execute(expired['id'],expired['confirmation'],now=2061)
    self.assertEqual(len(calls),1);self.assertIn('expired',[x['state'] for x in a.audit()['items']])
   finally:w.close()
 def test_uncertain_launch_is_never_repeated(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   w=Workbench(root,FakeHTTP())
   try:
    with patch('bili_workbench.computer_actions.subprocess.Popen',side_effect=OSError('fixture')) as launch:
     a=ComputerActions(w.s,path_resolver=lambda f:'fixture/'+f);d=a.prepare('notepad')
     with self.assertRaises(Fault):a.execute(d['id'],d['confirmation'])
     with self.assertRaises(Fault):a.execute(d['id'],d['confirmation'])
     self.assertEqual(launch.call_count,1);self.assertEqual(a.audit()['items'][0]['state'],'uncertain')
   finally:w.close()

if __name__=='__main__':unittest.main()
