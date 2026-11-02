"""Offline in-memory WebVTT provider using vendored webvtt-py 0.5.1."""
import hashlib
from vendor import webvtt
from boundary import Subtitle

class LocalVttProvider:
    def __init__(self,video_id,content):
        if not isinstance(video_id,str) or not video_id.strip() or not isinstance(content,str): raise ValueError('video/content')
        if len(content.encode('utf-8'))>1_000_000: raise ValueError('subtitle too large')
        self.__id=video_id; self.__content=content
        self.revision='webvtt-py-0.5.1:'+hashlib.sha256((video_id+'\0'+content).encode()).hexdigest()
    def get_subtitle(self,video_id):
        if video_id != self.__id: raise KeyError(video_id)
        parsed=webvtt.from_string(self.__content)
        return Subtitle(video_id,'\n'.join(c.text for c in parsed),'local WebVTT / webvtt-py 0.5.1')
