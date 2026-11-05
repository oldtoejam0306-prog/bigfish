import tempfile,unittest
from pathlib import Path
from bili_workbench.voice_probe import inspect_local, REQUIRED_MODEL_FILES

class VoiceProbeTests(unittest.TestCase):
    def test_missing_files_report_no_readiness_and_no_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);r=inspect_local(p/'python.exe',p/'source',p/'model')
            self.assertFalse(r['required_files_present']);self.assertFalse(r['model_started']);self.assertFalse(r['network_called'])
            self.assertEqual(set(r['missing_model_files']),set(REQUIRED_MODEL_FILES))
    def test_present_files_do_not_claim_runtime_or_voice_verified(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);python=p/'python.exe';python.touch();source=p/'source';f=source/'cosyvoice'/'cli'/'cosyvoice.py';f.parent.mkdir(parents=True);f.touch()
            model=p/'model';model.mkdir();(model/'CosyVoice-BlankEN').mkdir()
            for name in REQUIRED_MODEL_FILES:(model/name).touch()
            r=inspect_local(python,source,model)
            self.assertTrue(r['required_files_present']);self.assertFalse(r['runtime_verified']);self.assertFalse(r['voice_quality_verified']);self.assertFalse(r['model_started']);self.assertFalse(r['network_called'])

if __name__=='__main__':unittest.main()
