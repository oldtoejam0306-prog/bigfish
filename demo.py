import json
import tempfile
from pathlib import Path
from app import BigFish
from boundary import FixtureSubtitleProvider
with tempfile.TemporaryDirectory() as tmp:
    provider=FixtureSubtitleProvider({'demo':'这是离线示例字幕。'})
    app=BigFish(Path(tmp)/'bigfish.db',provider,provider.revision); session=app.local_owner_session()
    print('人格',json.dumps(session.personality(),ensure_ascii=False))
    session.remember('long','喜欢讨论画画','本地用户明确告知',0.95)
    print('记忆',json.dumps(session.memories(),ensure_ascii=False))
    print('离线阅读',json.dumps(session.read_fixture('demo','request-1'),ensure_ascii=False))
    print('重复命中缓存',session.read_fixture('demo','request-1')['cached'])
    app.close()
