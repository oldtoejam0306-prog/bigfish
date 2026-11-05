import subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bili_workbench.runtime import Vault,Fault
from bili_workbench.service import Workbench,extract_frames
from bili_workbench.media_tools import find_media_tools
from test_workbench import FakeHTTP

BV='BV1eRJS67EJP'
def subtitle(text='模拟字幕'):
 return {'bvid':BV,'cid':123,'part_index':1,'parts_total':1,'title':'模拟视频','text':text,'content_revision':'fixture'}

class LocalVideoIntegrationTests(unittest.TestCase):
 def setUp(self):
  ffmpeg,ffprobe=find_media_tools()
  if not ffmpeg or not ffprobe:self.skipTest('FFmpeg/ffprobe unavailable')
  self.tmp=tempfile.TemporaryDirectory();self.file=Path(self.tmp.name)/'synthetic.mp4'
  subprocess.run([ffmpeg,'-nostdin','-hide_banner','-v','error','-f','lavfi','-i','testsrc=size=128x72:rate=10','-t','3','-c:v','mpeg4','-pix_fmt','yuv420p',str(self.file)],capture_output=True,timeout=30,check=True)
  self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data);self.vault.start();self.http=FakeHTTP();self.w=Workbench(Path(self.tmp.name)/'data',self.http)
  self.w.s.configure({'provider_url':'https://example.test/v1','model':'fixture','vision_model':'fixture-vision','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'vision_input_price_micro_per_million':200000,'vision_output_price_micro_per_million':800000,'vision_price_version':'fixture-CNY','max_input_tokens':2048,'max_output_tokens':128})
 def tearDown(self):self.w.close();self.vault.stop();self.tmp.cleanup()
 def test_real_frames_reach_only_fixture_model_after_confirmation(self):
  result={'text':'模拟视觉结果','model':'fixture','cached':False,'model_called':True}
  with patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w.s.vault,'read',side_effect=AssertionError('no credentials')) as vault,patch.object(self.w.model,'plan',return_value={'ready':True}),patch.object(self.w.model,'summarize',return_value=result) as model:
   plan=self.w.video_preparation.prepare(BV,'frames',1,str(self.file));self.assertTrue(plan['ready'],plan['blockers']);self.assertFalse(plan['model_called']);model.assert_not_called()
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'')
   done=self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
   frames=model.call_args.args[2];self.assertGreaterEqual(len(frames),1);self.assertTrue(all(x.startswith('data:image/jpeg;base64,') for x in frames))
   self.assertFalse(done['result']['result']['sampling']['full_video_watched']);self.assertEqual(model.call_count,1);vault.assert_not_called()
  self.assertEqual(self.http.calls,[])
 def test_subtitle_changed_after_confirmation_is_blocked(self):
  with patch.object(self.w.bili,'subtitle',side_effect=[subtitle(),subtitle(),subtitle('最后一刻改变')]),patch.object(self.w.model,'summarize') as model:
   plan=self.w.video_preparation.prepare(BV,'frames',1,str(self.file));self.assertTrue(plan['ready'],plan['blockers'])
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
   model.assert_not_called()
  self.assertEqual(self.http.calls,[])
 def test_video_changed_during_extraction_is_blocked(self):
  def extraction_then_change(*args,**kwargs):
   value=extract_frames(*args,**kwargs)
   with self.file.open('ab') as stream:stream.write(b'fixture changed')
   return value
  with patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w.model,'plan',return_value={'ready':True}),patch('bili_workbench.service.extract_frames',side_effect=extraction_then_change),patch.object(self.w.model,'summarize') as model:
   plan=self.w.video_preparation.prepare(BV,'frames',1,str(self.file));self.assertTrue(plan['ready'],plan['blockers'])
   with self.assertRaises(Fault):self.w.video_preparation.execute(plan['id'],'确认最多调用1次模型')
   model.assert_not_called()
  self.assertEqual(self.http.calls,[])
 def test_invalid_local_container_is_rejected_during_preparation(self):
  self.file.write_bytes(b'not a video')
  with patch.object(self.w.bili,'subtitle',return_value=subtitle()),patch.object(self.w.model,'summarize') as model:
   plan=self.w.video_preparation.prepare(BV,'frames',1,str(self.file));self.assertFalse(plan['ready']);self.assertIn('本地视频不可访问或格式不支持',plan['blockers']);model.assert_not_called()
  self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
