import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class ModelZeroBudgetTests(unittest.TestCase):
 def test_disabled_does_not_read_vault_or_contact_model(self):
  with tempfile.TemporaryDirectory() as root,patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   try:
    with patch.object(w.s.vault,'read',side_effect=AssertionError('should not read')) as read:
     self.assertFalse(w.model.plan()['ready'])
     with self.assertRaises(Fault):w.model.complete('fixture','fixture-01')
     w.s.configure({'paid_enabled':True,'daily_budget_micro':0,'request_cap_micro':100,'provider_url':'https://example.test/v1','model':'fixture'})
     self.assertFalse(w.model.plan()['ready'])
     with self.assertRaises(Fault):w.model.complete('fixture','fixture-02')
     read.assert_not_called()
    self.assertEqual(http.calls,[])
   finally:w.close()

if __name__=='__main__':unittest.main()
