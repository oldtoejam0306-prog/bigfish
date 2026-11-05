import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bili_workbench.media_tools import find_media_tools

class MediaToolsTests(unittest.TestCase):
 def test_explicit_folder_is_checked_and_never_silently_falls_back(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'BIGFISH_FFMPEG_DIR':root}):
   with patch('bili_workbench.media_tools.shutil.which') as fallback:
    self.assertEqual(find_media_tools(),(None,None));fallback.assert_not_called()
    names=('ffmpeg.exe','ffprobe.exe') if os.name=='nt' else ('ffmpeg','ffprobe')
    for name in names:(Path(root)/name).write_bytes(b'fixture, not executable')
    found=find_media_tools();self.assertTrue(all(Path(x).is_file() for x in found));fallback.assert_not_called()
 def test_dedicated_bundle_is_auto_discovered_without_path_changes(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root},clear=False):
   os.environ.pop('BIGFISH_FFMPEG_DIR',None)
   bundle=Path(root)/'BigFish'/'tools'/'ffmpeg-9.0.2';bundle.mkdir(parents=True)
   for name in ('ffmpeg.exe','ffprobe.exe'):(bundle/name).write_bytes(b'fixture')
   original_path=os.environ.get('PATH')
   with patch('bili_workbench.media_tools.shutil.which') as fallback:
    found=find_media_tools();self.assertEqual(found,(str(bundle/'ffmpeg.exe'),str(bundle/'ffprobe.exe')));fallback.assert_not_called()
   self.assertEqual(os.environ.get('PATH'),original_path)
 def test_incomplete_bundle_uses_normal_path_fallback(self):
  with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'LOCALAPPDATA':root},clear=False):
   os.environ.pop('BIGFISH_FFMPEG_DIR',None)
   bundle=Path(root)/'BigFish'/'tools'/'ffmpeg-9.0.2';bundle.mkdir(parents=True);(bundle/'ffmpeg.exe').write_bytes(b'fixture')
   with patch('bili_workbench.media_tools.shutil.which',side_effect=lambda x:'fixture/'+x):self.assertEqual(find_media_tools(),('fixture/ffmpeg','fixture/ffprobe'))
 def test_default_path_lookup(self):
  with patch.dict(os.environ,{},clear=False):
   os.environ.pop('BIGFISH_FFMPEG_DIR',None);os.environ.pop('LOCALAPPDATA',None)
   with patch('bili_workbench.media_tools.shutil.which',side_effect=lambda x:'fixture/'+x):
    self.assertEqual(find_media_tools(),('fixture/ffmpeg','fixture/ffprobe'))

if __name__=='__main__':unittest.main()
