import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault,canonical
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class BiliLoginDiagnosticsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.w.s.configure({'account_mid':'123','read_enabled':True});self.w.s.vault.save({'cookie':'SESSDATA=synthetic-only; bili_jct=synthetic-csrf'});self.w.s.gate=lambda *a,**k:None
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_network_failure_retains_reason_without_leaking_or_retrying(self):
  calls=[]
  def fail(*a,**k):calls.append(1);raise Fault('网络失败或结果不确定，请勿自动重试写入')
  self.http.json=fail;status=self.w.bili.login_status(True);self.assertEqual((status['state'],status['reason']),('unknown','network_error'));self.assertNotIn('synthetic-only',canonical(status))
  with self.assertRaisesRegex(Fault,'网络连接失败'):self.w.bili.ensure_read_identity()
  self.assertEqual(len(calls),1)
 def test_platform_rate_limit_is_distinguished_and_still_pauses(self):
  def fail(*a,**k):raise Fault('HTTP 429')
  self.http.json=fail;status=self.w.bili.login_status(True);self.assertEqual(status['reason'],'HTTP 429');self.assertEqual(status['state'],'unknown')
  with self.assertRaisesRegex(Fault,'HTTP 429'):self.w.bili.ensure_read_identity()

 def test_explicit_popular_request_rechecks_transient_unknown_once(self):
  old={'credential_present':True,'state':'unknown','verified':False,'reason':'network_error'}
  with patch.object(self.w.bili,'login_status',side_effect=[old,{'state':'valid','verified':True}]) as status,patch.object(self.w.bili,'feed',return_value={'list':[]}) as feed:
   self.assertEqual(self.w.read_popular(),{'list':[]});self.assertEqual(status.call_count,2);status.assert_any_call(probe=True);feed.assert_called_once_with()
 def test_explicit_request_does_not_recheck_a_risk_response(self):
  old={'credential_present':True,'state':'unknown','verified':False,'reason':'HTTP 429'}
  with patch.object(self.w.bili,'login_status',return_value=old) as status,patch.object(self.w.bili,'feed',side_effect=Fault('HTTP 429')):
   with self.assertRaises(Fault):self.w.read_popular()
   status.assert_called_once_with()

if __name__=='__main__':unittest.main()
