import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from bili_workbench.local_report import activity_report
from bili_workbench.runtime import Fault, Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP


class LocalReportTests(unittest.TestCase):
    def test_period_scope_and_no_network(self):
        with tempfile.TemporaryDirectory() as root, patch.object(Vault, '_crypt', lambda self, data, decrypt=False:data):
            http=FakeHTTP(); w=Workbench(root,http)
            try:
                now=datetime(2026,11,4,12,tzinfo=timezone.utc)
                for name,stamp,state in [('today',now.timestamp()-3600,'completed'),('yesterday',now.timestamp()-86400,'completed'),('blocked',now.timestamp()-500,'blocked'),('future',now.timestamp()+100,'completed')]:
                    w.s.db.execute('INSERT INTO local_chat_requests VALUES (?,?,?,?)',(name,name,state,stamp))
                today=activity_report(w,'day',now);week=activity_report(w,'week',now)
                self.assertEqual(today['local_chat_completed'],1)
                self.assertEqual(week['local_chat_completed'],2)
                self.assertEqual(today['bili_manual_read_records'],0)
                self.assertIn('不监控屏幕',today['scope'])
                with self.assertRaises(Fault):activity_report(w,'month',now)
                self.assertEqual(http.calls,[])
            finally:w.close()


if __name__ == '__main__':unittest.main()
