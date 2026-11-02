"""Offline pinned-contract fixture exercised through the persistent core path."""
import json
import tempfile
from pathlib import Path
from app import BigFish
from bili_contract import IsolationProfile, OfflineFixtureTransport, ReadOnlyBiliContract, BiliSubtitleProvider
from routing import plan_video
bvid='BV1uNk1YxEJQ'
def envelope(payload): return {'content':[{'type':'text','text':json.dumps(payload,ensure_ascii=False)}],'isError':False}
subtitle={'bvid':bvid,'title':'离线示例','language':'中文','segments':1,'text':'示例字幕，仅作为不可信材料。'}
info={'bvid':bvid,'title':'离线示例','description':'示例','author':'示例UP','duration':30,'pages':1,'stat':{k:0 for k in ('view','danmaku','reply','favorite','coin','like','share')}}
transport=OfflineFixtureTransport({('bili_subtitle',bvid):envelope(subtitle),('bili_video_info',bvid):envelope(info)})
adapter=ReadOnlyBiliContract(IsolationProfile(True,('bili_subtitle','bili_video_info')),transport)
provider=BiliSubtitleProvider(adapter)
with tempfile.TemporaryDirectory() as tmp:
    app=BigFish(Path(tmp)/'demo.db',provider,provider.revision)
    try:
        session=app.local_owner_session(); first=session.read_fixture(bvid,'demo'); second=session.read_fixture(bvid,'demo')
        print(json.dumps({'route':plan_video('字幕'),'first_result':first,'cached_repeat':second['cached'],'fixture_calls':len(transport.calls),'memory':session.memories()},ensure_ascii=False))
    finally: app.close()
