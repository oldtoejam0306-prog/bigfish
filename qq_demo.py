"""Offline OneBot-shaped example; numbers are synthetic."""
import json
import tempfile
from pathlib import Path
from qq_offline import OfflineConfig,FixtureEventSource,MockModel,OfflineQQ
cfg=OfflineConfig(10001,20002)
event={'time':1700000000,'self_id':10001,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'user_id':20002,'message':[{'type':'text','data':{'text':'你好'}}]}
with tempfile.TemporaryDirectory() as tmp:
    qq=OfflineQQ(Path(tmp)/'fish.db',cfg,FixtureEventSource(cfg),MockModel())
    try:
        print(json.dumps(qq.receive(event),ensure_ascii=False))
        print(json.dumps(qq.receive(event),ensure_ascii=False))
    finally: qq.close()
