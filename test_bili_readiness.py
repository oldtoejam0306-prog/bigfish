import tempfile
import unittest
from unittest.mock import patch
from bili_workbench.service import Workbench
from bili_workbench.runtime import Fault,canonical
from test_workbench import FakeHTTP,BV

class ReadHTTP(FakeHTTP):
    def __init__(self):
        super().__init__();self.nav_mid=123;self.logged_in=True;self.no_sub=False;self.subtitle_text='模拟字幕';self.downloads=[];self.dash=None
        self.items=[{'bvid':'BV'+str(i).zfill(10),'title':'模拟热门'} for i in range(1,6)]
    def json(self,method,url,headers=None,payload=None,form=False):
        if '/nav' in url:
            result=super().json(method,url,headers,payload,form)
            result['data'].update(mid=self.nav_mid,isLogin=self.logged_in);return result
        if '/popular' in url:self.calls.append((method,url,headers,payload));return {'code':0,'data':{'list':self.items}}
        if '/playurl' in url:
            self.calls.append((method,url,headers,payload));return {'code':0,'data':{'dash':self.dash}}
        result=super().json(method,url,headers,payload,form)
        if '/x/player/wbi/v2' in url and self.no_sub:result={'code':0,'data':{'subtitle':{'subtitles':[]}}}
        if 'sub.json' in url:result['body'][0]['content']=self.subtitle_text
        return result
    def request(self,method,url,headers=None,body=None,limit=4000000):
        self.downloads.append((method,url,headers,limit));return b'\x00\x00\x00\x18ftypisom',{}

class BiliReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=ReadHTTP();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
        self.s.configure({'read_enabled':True,'account_mid':'123'})
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def configured(self):
        self.s.configure({'provider_url':'https://example.test/v1','model':'cheap','vision_model':'cheap-vision','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY','vision_price_version':'fixture-vision-CNY','vision_input_price_micro_per_million':200000,'vision_output_price_micro_per_million':800000})
        self.s.vault.save({'api_key':'fixture-secret'})
    def active(self):self.s.db.execute('UPDATE browse_control SET enabled=1,next_at=0')
    def dash(self):return {'duration':10,'video':[{'bandwidth':500000,'codecs':'avc1.640028','baseUrl':'https://fixture.bilivideo.com/high.m4s'},{'bandwidth':200000,'codecs':'avc1.640028','baseUrl':'https://fixture.bilivideo.com/low.m4s'}]}
    def test_cancelled_frame_task_never_starts_decoder(self):
        from bili_workbench.service import extract_frames
        with patch('bili_workbench.service.subprocess.run') as run:
            with self.assertRaises(Fault):extract_frames('not-opened.mp4',cancel_check=lambda:True)
            run.assert_not_called()
    def test_cached_login_becomes_expired_when_signed_api_reports_logout(self):
        self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture'})
        self.assertTrue(self.w.bili.login_status(True)['verified'])
        original=self.http.json
        def logged_out(*args,**kwargs):
            value=original(*args,**kwargs)
            if '/nav' in args[1]:value['code']=-101
            return value
        self.http.json=logged_out
        with self.assertRaises(Fault):self.w.bili.info(BV)
        self.assertEqual(self.w.bili.login_status()['state'],'expired')
        self.assertFalse(any('/wbi/view' in url for method,url,*rest in self.http.calls))
    def test_visual_oversize_text_rejects_before_media_download(self):
        self.configured();self.http.subtitle_text='长'*6000
        result=self.w._confirmed_analyze(BV,'too-long','remote_frames')
        self.assertEqual(result['status'],'needs_model_configuration');self.assertEqual(self.http.downloads,[])
    def test_subtitle_scope_names_selected_part(self):
        subtitle=self.w.bili.subtitle(BV)
        self.assertEqual(subtitle['cid'],200);self.assertEqual(subtitle['part_index'],1)
        self.assertFalse(subtitle['full_video_watched'])
    def test_queue_replan_requires_per_video_confirmation_before_frames(self):
        self.configured();self.http.no_sub=True;self.http.items=[{'bvid':BV,'title':'模拟视频'}]
        self.active();self.w.queue.tick();old=self.w.queue.state()['jobs'][0]
        self.s.configure({'browse_visual_fallback':True});self.http.dash=self.dash()
        queued=self.w.queue.retry_job(old['key']);self.assertNotEqual(queued['key'],old['key'])
        with patch('bili_workbench.service.extract_frames',return_value=['data:image/jpeg;base64,fixture']),patch('bili_workbench.service.shutil.which',return_value='/fixture/binary'):
            self.active();self.w.queue.tick()
        self.assertEqual(self.s.db.execute('SELECT coverage FROM public_video_observations WHERE bvid=?',(BV,)).fetchone()[0],'metadata')
        self.assertEqual(self.w.queue.state()['jobs'][0]['state'],'needs_input');self.assertEqual(self.http.downloads,[])
    def test_presence_is_not_verified_and_wrong_account_blocks_read(self):
        self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture'})
        self.assertEqual(self.w.bili.login_status()['state'],'unchecked');self.assertEqual(self.http.calls,[])
        self.http.nav_mid=999
        with self.assertRaises(Fault):self.w.bili.subtitle(BV)
        self.assertEqual(self.w.bili.login_status()['state'],'account_mismatch')
        self.assertEqual(len(self.http.calls),1);self.assertEqual(self.http.downloads,[])
    def test_expired_or_malformed_login_pauses_not_anonymous_fallback(self):
        self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture'})
        self.assertTrue(self.w.bili.login_status(True)['verified'])
        self.http.logged_in=False;self.assertEqual(self.w.bili.login_status(True)['state'],'expired')
        self.active();self.w.queue.tick()
        self.assertFalse(self.w.queue.state()['enabled']);self.assertEqual(self.w.queue.state()['jobs'],[])
        self.http.logged_in=True;self.http.nav_mid=True
        self.assertFalse(self.w.bili.login_status(True)['verified'])
    def test_no_cookie_is_explicit_anonymous_not_fake_login(self):
        status=self.w.bili.login_status(True)
        self.assertEqual(status['state'],'not_configured');self.assertFalse(status['verified']);self.assertEqual(self.http.calls,[])
        self.w.bili.info(BV);self.assertTrue(self.http.calls)
    def test_subtitle_content_cache_refresh_and_revision(self):
        first=self.w.bili.subtitle(BV);n=len(self.http.calls)
        second=self.w.bili.subtitle(BV);self.assertTrue(second['cached']);self.assertEqual(len(self.http.calls),n)
        self.http.subtitle_text='更新模拟字幕';third=self.w.bili.subtitle(BV,refresh=True)
        self.assertNotEqual(first['content_revision'],third['content_revision']);self.assertFalse(third['full_video_watched'])
    def test_dash_chooses_small_avc_track_without_cookie_or_audio(self):
        self.http.dash=self.dash();self.s.vault.save({'cookie':'SESSDATA=fixture; bili_jct=fixture'})
        self.w.bili.media(BV)
        self.assertEqual(len(self.http.downloads),1);download=self.http.downloads[0]
        self.assertIn('/low.m4s',download[1]);self.assertNotIn('Cookie',download[2]);self.assertEqual(download[3],50000000)
    def test_dash_rejects_unsafe_or_unknown_cost_before_download(self):
        for dash in ({'duration':float('nan'),'video':[]},{'duration':10000,'video':[{'bandwidth':999999999,'codecs':'avc1','baseUrl':'https://fixture.bilivideo.com/v'}]},{'duration':1,'video':[{'bandwidth':100,'codecs':'avc1','baseUrl':'https://evil.test/v'}]}):
            self.http.dash=dash
            with self.assertRaises(Fault):self.w.bili.media(BV)
        self.assertEqual(self.http.downloads,[])
    def test_plan_never_calls_provider_and_labels_estimate(self):
        self.configured();plan=self.w.model.plan(True)
        self.assertTrue(plan['ready']);self.assertEqual(plan['configured_max_cost_per_call_micro'],4000)
        self.assertIsNone(plan['real_usage']);self.assertFalse(plan['quote_is_provider_bill']);self.assertEqual(self.http.calls,[])
        self.s.configure({'request_cap_micro':1});self.assertFalse(self.w.model.plan()['ready'])
    def test_zero_budget_auto_does_not_download_or_infer(self):
        self.configured();self.http.no_sub=True;self.s.configure({'daily_budget_micro':0})
        result=self.w._confirmed_analyze(BV,'zero','auto')
        self.assertEqual(result['status'],'needs_model_configuration');self.assertEqual(self.http.downloads,[])
        self.assertFalse(any(url.endswith('/chat/completions') for method,url,*rest in self.http.calls))
    def test_auto_prefers_subtitle_without_media(self):
        self.configured();result=self.w._confirmed_analyze(BV,'subtitle','auto')
        self.assertEqual(result['result']['coverage'],'仅字幕');self.assertEqual(self.http.downloads,[])
    def test_auto_missing_subtitle_uses_and_caches_sparse_frames(self):
        self.configured();self.http.no_sub=True;self.http.dash=self.dash()
        with patch('bili_workbench.service.extract_frames',return_value=['data:image/jpeg;base64,fixture']) as extract,patch('bili_workbench.service.shutil.which',return_value='/fixture/binary'):
            a=self.w._confirmed_analyze(BV,'frames','auto');b=self.w._confirmed_analyze(BV,'frames-again','auto')
        self.assertFalse(a['result']['full_video_watched']);self.assertTrue(b['result']['cached'])
        self.assertEqual(len(self.http.downloads),1);self.assertEqual(extract.call_count,1)
    def test_queue_moves_past_first_three_seen_videos(self):
        self.active();self.w.queue.tick();self.assertEqual(len(self.w.queue.state()['jobs']),3)
        self.active();self.w.queue.tick();self.assertEqual(len(self.w.queue.state()['jobs']),5)
        self.active();self.w.queue.tick();self.assertEqual(len(self.w.queue.state()['jobs']),5)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_attempts').fetchone()[0],5)
    def test_queue_retry_still_requires_video_confirmation(self):
        self.configured();self.http.no_sub=True;self.http.items=[{'bvid':BV,'title':'无字幕fixture'}]
        self.active();self.w.queue.tick();job=self.w.queue.state()['jobs'][0]
        self.assertEqual(job['state'],'needs_input');self.assertFalse(self.w.queue.state()['enabled'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        self.http.no_sub=False;self.active();self.assertTrue(self.w.queue.retry_job(job['key'])['requires_explicit_resume'])
        self.assertFalse(self.w.queue.state()['enabled'])
        self.active();self.w.queue.tick();self.assertEqual(self.w.queue.state()['jobs'][0]['state'],'needs_input')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_attempts').fetchone()[0],2)
    def test_queue_visual_fallback_does_not_bypass_confirmation(self):
        self.configured();self.s.configure({'browse_visual_fallback':True});self.http.no_sub=True;self.http.dash=self.dash();self.http.items=[{'bvid':BV,'title':'模拟视频'}]
        with patch('bili_workbench.service.extract_frames',return_value=['data:image/jpeg;base64,fixture']),patch('bili_workbench.service.shutil.which',return_value='/fixture/binary'):
            self.active();self.w.queue.tick()
        self.assertEqual(self.w.queue.state()['jobs'][0]['state'],'needs_input');self.assertEqual(len(self.http.downloads),0)
    def test_unconfirmed_queue_preserves_prior_subtitle_observation(self):
        self.configured();self.http.items=[{'bvid':BV,'title':'模拟视频'}]
        self.w.interests.record_observation(BV,'subtitle','fixture previously read')
        self.active();self.w.queue.tick()
        row=self.s.db.execute('SELECT coverage FROM public_video_observations WHERE bvid=?',(BV,)).fetchone()
        self.assertEqual(row['coverage'],'subtitle');self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_unconfirmed_queue_never_calls_model_or_retries_automatically(self):
        self.configured();self.http.usage=False;self.http.items=[{'bvid':BV,'title':'模拟视频'}]
        self.active();self.w.queue.tick();job=self.w.queue.state()['jobs'][0]
        self.assertEqual(job['state'],'needs_input');calls=len(self.http.calls)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
        self.w.queue.tick();self.assertEqual(len(self.http.calls),calls)
    def test_retry_counts_attempts_and_plan_key_tracks_provider(self):
        self.configured();self.http.no_sub=True;self.http.items=[{'bvid':BV,'title':'模拟视频'}];self.s.configure({'browse_daily_limit':1})
        self.active();self.w.queue.tick();job=self.w.queue.state()['jobs'][0]
        self.w.queue.retry_job(job['key']);self.http.no_sub=False;self.active();self.w.queue.tick()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM browse_attempts').fetchone()[0],1)
        old=job['key'];self.s.configure({'provider_url':'https://another.test/v1'})
        self.assertNotEqual(self.w.queue.key(self.s.config(),BV),old)

if __name__=='__main__':unittest.main()
