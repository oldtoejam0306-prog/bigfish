"""Locate a user-specified FFmpeg bundle without changing the system PATH."""
import os
from pathlib import Path
import shutil


def find_media_tools():
    configured=os.environ.get('BIGFISH_FFMPEG_DIR')
    if configured:
        try:
            root=Path(configured).expanduser().resolve(strict=True)
            ffmpeg=root/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg')
            ffprobe=root/('ffprobe.exe' if os.name=='nt' else 'ffprobe')
            if root.is_dir() and ffmpeg.is_file() and ffprobe.is_file():return str(ffmpeg),str(ffprobe)
        except (OSError,ValueError):pass
        # An invalid explicit selection must not silently choose another binary.
        return None,None
    # Prefer the app's dedicated, locally installed bundle; never change PATH.
    if os.name=='nt' and os.environ.get('LOCALAPPDATA'):
        root=Path(os.environ['LOCALAPPDATA'])/'BigFish'/'tools'/'ffmpeg-9.0.2'
        ffmpeg,ffprobe=root/'ffmpeg.exe',root/'ffprobe.exe'
        if ffmpeg.is_file() and ffprobe.is_file():return str(ffmpeg),str(ffprobe)
    return shutil.which('ffmpeg'),shutil.which('ffprobe')
