import json,tempfile,time,unittest
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault,canonical
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

class BiliPendingLoginTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http);self.w.s.configure({'account_mid':'123','read_enabled':True});self.w.s.gate=lambda *a,**k:None;self.w.bili.qr={'key':'fixture','expires':time.time()+180};self.polls=0;self.navs=0
  def request(method,url,*a,**k):
   self.polls+=1;return canonical({'code':0,'data':{'code':0}}).encode(),{'_set_cookies':['SESSDATA=fixture-session; Path=/','bili_jct=fixture-csrf; Path=/']}
  def nav(method,url,*a,**k):
   self.navs+=1
   if self.navs==1:raise Fault('网络失败或结果不确定，请勿自动重试写入')
   return {'code':0,'data':{'isLogin':True,'mid':123}}
  self.http.request=request;self.http.json=nav
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_network_failure_after_qr_success_can_retry_identity_without_polling_again(self):
  first=self.w.bili.login_poll();self.assertEqual(first['state'],'verification_pending');self.assertNotIn('fixture-session',canonical(first));self.assertFalse(self.w.s.vault.read().get('cookie'))
  second=self.w.bili.login_poll();self.assertTrue(second['logged_in']);self.assertEqual(self.polls,1);self.assertEqual(self.navs,2);self.assertIsNone(self.w.bili._pending_login)
 def test_account_change_invalidates_pending_confirmation(self):
  self.w.bili.login_poll();self.w.s.configure({'account_mid':'456'})
  with self.assertRaises(Fault):self.w.bili.login_poll()
  self.assertIsNone(self.w.bili._pending_login);self.assertEqual(self.polls,1);self.assertFalse(self.w.s.vault.read().get('cookie'))
 def test_expired_remote_code_clears_old_qr(self):
  self.http.request=lambda *a,**k:(canonical({'code':0,'data':{'code':86038}}).encode(),{})
  result=self.w.bili.login_poll();self.assertEqual(result['code'],86038);self.assertIsNone(self.w.bili.qr)

if __name__=='__main__':unittest.main()
