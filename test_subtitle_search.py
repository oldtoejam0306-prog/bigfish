import tempfile,unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

BV='BV1eRJS67EJP'
def data(part=1):
 return {'bvid':BV,'part_index':part,'segments':[{'from':1.2,'to':3,'content':'鲸鱼是哺乳动物。'},{'from':65,'to':67,'content':'<script>鲸鱼</script>'},{'from':70,'to':71,'content':'别的内容'}]}

class SubtitleSearchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_search_is_session_only_and_never_calls_network_or_model(self):
  with patch.object(self.w.s.vault,'read',side_effect=AssertionError('vault read')),patch.object(self.w.model,'chat') as model:
   with self.assertRaises(Fault):self.w.subtitle_search.search(BV,1,'鲸鱼')
   self.w.subtitle_search.load(BV,1,data());result=self.w.subtitle_search.search(BV,1,'鲸鱼')
   self.assertEqual(result['total'],2);self.assertEqual([x['second'] for x in result['matches']],[1,65]);self.assertFalse(result['model_called']);self.assertFalse(result['network_called'])
   self.assertEqual(self.w.subtitle_search.search(BV,1,'不存在')['total'],0)
   from bili_workbench.subtitle_search import SubtitleSearch
   with self.assertRaises(Fault):SubtitleSearch(self.w.s).search(BV,1,'鲸鱼')
   with self.assertRaises(Fault):self.w.subtitle_search.search(BV,2,'鲸鱼')
   with self.assertRaises(Fault):self.w.subtitle_search.search('https://evil.test/video/'+BV,1,'鲸鱼')
   self.w.s.configure({'account_mid':'123'})
   with self.assertRaises(Fault):self.w.subtitle_search.search(BV,1,'鲸鱼')
   model.assert_not_called()
  self.assertEqual(self.http.calls,[])
 def test_limit_and_subtitle_integration(self):
  sample=data(2);sample['segments']=[{'from':i,'to':i+1,'content':'测试 '+str(i)} for i in range(55)];sample['text']='测试';sample['title']='fixture'
  with patch.object(self.w.bili,'subtitle',return_value=sample):
   self.w.read_video_subtitle(BV,part_index=2)
  result=self.w.subtitle_search.search('https://www.bilibili.com/video/'+BV+'?p=2',2,'测试')
  self.assertEqual(result['total'],55);self.assertTrue(result['truncated']);self.assertEqual(len(result['matches']),50)
  page1=self.w.subtitle_search.page(BV,2,0);page2=self.w.subtitle_search.page(BV,2,page1['next_index']);page3=self.w.subtitle_search.page(BV,2,page2['next_index'])
  self.assertEqual([len(page1['matches']),len(page2['matches']),len(page3['matches'])],[20,20,15]);self.assertIsNone(page3['next_index'])
  with self.assertRaises(Fault):self.w.subtitle_search.page(BV,2,-1)
  self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
