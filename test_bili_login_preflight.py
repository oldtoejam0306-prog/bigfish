import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class BiliLoginPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
    def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
    def test_missing_uid_fails_before_qr_network(self):
        with self.assertRaisesRegex(Fault,'UID'):self.w.bili.login_start()
        self.assertEqual(self.http.calls,[]);self.assertIsNone(self.w.bili.qr)

if __name__=='__main__':unittest.main()
