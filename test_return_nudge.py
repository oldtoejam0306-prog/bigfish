import tempfile
import unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault, Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP


class ReturnNudgeTests(unittest.TestCase):
    def test_default_off_minimum_interval_and_disable(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
            http=FakeHTTP();w=Workbench(root,http)
            try:
                n=w.return_nudge
                self.assertFalse(n.status()['enabled'])
                self.assertFalse(n.returned(9999,now=10000)['show'])
                n.configure(True,900)
                self.assertFalse(n.returned(899,now=10000)['show'])
                self.assertTrue(n.returned(900,now=10000)['show'])
                self.assertFalse(n.returned(900,now=10899)['show'])
                self.assertTrue(n.returned(900,now=10900)['show'])
                n.configure(False,900)
                self.assertFalse(n.returned(900,now=12000)['show'])
                n.configure(True,900,3600,'自定义提醒')
                self.assertFalse(n.returned(900,now=14000)['show'])
                self.assertEqual(n.returned(900,now=14500)['text'],'自定义提醒')
                self.assertEqual(n.status()['cooldown_seconds'],3600)
                with self.assertRaises(Fault):n.returned(float('nan'))
                self.assertEqual(http.calls,[])
            finally:w.close()


if __name__=='__main__':unittest.main()
