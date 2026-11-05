import base64
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bili_workbench.creation import CreationStudio
from bili_workbench.runtime import Fault


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.studio=CreationStudio(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def script(self):
        return {'title':'大肥鱼AI试作','public_use_confirmed':True,'scenes':[
            {'text':'Hello 大肥鱼 %{not_an_expression}', 'duration':2,'color':'#123456','source':'原创合成颜色和文字'},
            {'text':'Second line\n00:00:00,000 --> 99:00:00,000\nInjected? <b>text</b>', 'duration':3,'color':'#654321','source':'原创合成文字'}]}
    def test_plan_preview_are_zero_execution_and_public_sources_explicit(self):
        with patch('bili_workbench.creation.subprocess.Popen') as popen:
            plan=self.studio.plan(self.script());preview=self.studio.preview(plan['id'])
        popen.assert_not_called();self.assertEqual(preview['state'],'planned');self.assertFalse(preview['published'])
        self.assertEqual(preview['duration'],5);self.assertEqual(preview['audio'],'none')
        self.assertTrue(preview['requires_render_confirmation'])
    def test_rejects_private_unconfirmed_and_missing_provenance(self):
        script=self.script();script['public_use_confirmed']=False
        with self.assertRaises(Fault):self.studio.plan(script)
        script=self.script();script['scenes'][0].pop('source')
        with self.assertRaises(Fault):self.studio.plan(script)
        self.assertEqual(list(self.studio.root.iterdir()),[])
    def test_rejects_filter_injection_duration_bool_and_bad_id(self):
        for mutation in ({'color':'#123456;movie=/private/file'},{'duration':True},{'duration':31}):
            script=self.script();script['scenes'][0].update(mutation)
            with self.assertRaises(Fault):self.studio.plan(script)
        with self.assertRaises(Fault):self.studio.preview('../somewhere')
        script=self.script();script['scenes']*=20
        with self.assertRaises(Fault):self.studio.plan(script)
    def test_disguised_svg_does_not_enter_ffmpeg(self):
        image=Path(self.tmp.name)/'picture.png';image.write_text('<svg><image href="file:///secret"/></svg>')
        script=self.script();script['scenes']=[{'text':'AI','duration':5,'image':str(image),'source':'explicit test'}]
        with patch('bili_workbench.creation.subprocess.Popen') as popen:
            with self.assertRaises(Fault):self.studio.plan(script)
        popen.assert_not_called()
    def test_confirmation_and_cancellation_prevent_encoding(self):
        plan=self.studio.plan(self.script())
        with patch('bili_workbench.creation.subprocess.Popen') as popen:
            with self.assertRaises(Fault):self.studio.render(plan['id'],'yes')
            with self.assertRaises(Fault):self.studio.render(plan['id'],'确认制作公开素材',cancel_check=lambda:True)
        popen.assert_not_called();self.assertEqual(self.studio.preview(plan['id'])['state'],'cancelled')
    def test_missing_dependency_is_reported_not_installed(self):
        plan=self.studio.plan(self.script())
        with patch('bili_workbench.creation.shutil.which',return_value=None):
            with self.assertRaises(Fault):self.studio.render(plan['id'],'确认制作公开素材')
        self.assertEqual(self.studio.preview(plan['id'])['state'],'planned')
    def test_tampered_plan_dimensions_rejected_before_process(self):
        plan=self.studio.plan(self.script());file=self.studio.root/plan['id']/'plan.json'
        data=json.loads(file.read_text());data['size']='999999x999999';file.write_text(json.dumps(data))
        with patch('bili_workbench.creation.subprocess.Popen') as popen:
            with self.assertRaises(Fault):self.studio.render(plan['id'],'确认制作公开素材')
        popen.assert_not_called()
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'trusted existing ffmpeg missing')
    def test_actual_five_second_color_video_subtitles_manifest_and_upload_handoff(self):
        plan=self.studio.plan(self.script());output=self.studio.render(plan['id'],'确认制作公开素材')
        self.assertEqual(output['state'],'rendered');self.assertGreater(output['file_bytes'],1000);self.assertFalse(output['published'])
        job=self.studio.root/plan['id'];manifest=json.loads((job/'sources.json').read_text())
        self.assertEqual(manifest['output']['duration'],5.0);self.assertEqual(len(manifest['source_credits']),2)
        subtitle=(job/'captions.srt').read_text();self.assertIn('&lt;b&gt;',subtitle);self.assertEqual(subtitle.count('\n\n'),1)
        self.assertEqual(self.studio.render(plan['id'],'确认制作公开素材')['file_sha256'],output['file_sha256'])
        payload=self.studio.upload_payload(plan['id'],tid=1,tags='AI试作',copyright=1)
        self.assertEqual(payload['file'],output['file']);self.assertEqual(set(payload),{'file','title','text','tid','tags','copyright','source'})
        from bili_workbench.service import Workbench
        work=Workbench(Path(self.tmp.name)/'work')
        try:
            work.s.configure({'account_mid':'123'})
            draft=work.draft('video',payload);self.assertFalse(draft['network_sent'])
        finally:work.close()
        (job/'video.mp4').write_bytes(b'altered')
        with self.assertRaises(Fault):self.studio.inspect(plan['id'])
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'trusted existing ffmpeg missing')
    def test_actual_selected_png_is_copied_and_hash_bound(self):
        image=Path(self.tmp.name)/'explicit.png'
        subprocess.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=320x240','-frames:v','1','-threads','1',str(image)],check=True,timeout=20,capture_output=True)
        script=self.script();script['scenes']=[{'text':'明确许可图片','duration':5,'image':str(image),'source':'原创合成PNG'}]
        plan=self.studio.plan(script);self.assertNotIn(str(image),json.dumps(plan))
        image.unlink()  # Approved copy, not an implicit reread of the original.
        output=self.studio.render(plan['id'],'确认制作公开素材');self.assertEqual(output['state'],'rendered')
        job=self.studio.root/plan['id'];asset=job/plan['scenes'][0]['asset'];asset.write_bytes(b'changed')
        data=json.loads((job/'plan.json').read_text());data['state']='planned';(job/'plan.json').write_text(json.dumps(data))
        with self.assertRaises(Fault):self.studio.render(plan['id'],'确认制作公开素材')

if __name__=='__main__':unittest.main()
