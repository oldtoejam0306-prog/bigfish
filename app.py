"""Offline owner-only runtime. This is not a QQ authentication adapter or sandbox."""
import hashlib
import json
from boundary import Store, VideoReader
from core import Core

class BigFish:
    def __init__(self,path,provider,provider_revision):
        if not isinstance(provider_revision,str) or not provider_revision.strip(): raise ValueError('fixed provider revision required')
        self._core=Core(path); self._store=Store(path)
        self._reader=VideoReader(provider); self._revision=provider_revision; self.__session_key=object()
    def close(self): self._store.close(); self._core.close()
    def local_owner_session(self):
        # Only this local interactive adapter may create a session. There is no
        # network identity authentication or arbitrary chat-scope constructor.
        return OwnerSession(self,self.__session_key)

class OwnerSession:
    def __init__(self, app, key):
        if key is not app._BigFish__session_key: raise PermissionError('trusted adapter required')
        self.__app=app; self.__scope=('friend','local-owner')
    def remember(self,layer,content,source,confidence):
        return self.__app._store.add_memory(*self.__scope,layer,content,source,confidence)
    def memories(self): return self.__app._store.memories(*self.__scope)
    def change_memory(self,ident,source,new_content=None):
        return self.__app._store.change_memory(*self.__scope,ident,source,new_content)
    def personality(self): return self.__app._core.personality()
    def correct(self,instruction,data): return self.__app._core.correct(instruction,data)
    def read_fixture(self,video_id,request_id,model_version='fixture-v1',prompt_version='v1',simulated_cost=0):
        if not all(isinstance(x,str) and x.strip() for x in (video_id,request_id,model_version,prompt_version)): raise ValueError('request')
        if type(simulated_cost) is not int or simulated_cost<0: raise ValueError('simulated cost')
        app=self.__app; store=app._store
        # Provider revision is trusted configuration fixed before a request. For
        # immutable fixtures it hashes all content; never call provider to get it.
        payload=json.dumps([*self.__scope,video_id,app._revision,model_version,prompt_version,simulated_cost],ensure_ascii=False)
        binding=hashlib.sha256(payload.encode()).hexdigest()
        rid=hashlib.sha256(json.dumps([*self.__scope,request_id],ensure_ascii=False).encode()).hexdigest()
        store.bind_request(rid,binding)
        cached=store.cache_get(payload,model_version,prompt_version)
        if cached is not None: return dict(cached,cached=True)
        state=store.reserve(rid,simulated_cost)
        if state != 'reserved': raise RuntimeError('request already resolved or uncertain; reconcile manually')
        store.db.execute('BEGIN IMMEDIATE')
        try:
            old=store.db.execute('SELECT state FROM executions WHERE request_id=?',(rid,)).fetchone()
            if old: raise RuntimeError('request execution uncertain; reconcile manually')
            store.db.execute("INSERT INTO executions VALUES (?,'started')",(rid,)); store.db.commit()
        except Exception: store.db.rollback(); raise
        # From this point a failure is uncertain: no automatic repeat or release.
        item=app._reader.read(video_id)
        result=dict(item,simulated=True,simulated_cost_micro=simulated_cost)
        # Atomic local commit: no crash window between settlement and cache.
        store.db.execute('BEGIN IMMEDIATE')
        try:
            row=store.db.execute('SELECT * FROM reservations WHERE request_id=?',(rid,)).fetchone()
            if row['state']!='reserved' or simulated_cost>row['amount']: raise RuntimeError('invalid reservation')
            store.db.execute("UPDATE reservations SET state='settled',actual=? WHERE request_id=?",(simulated_cost,rid))
            key=store.cache_key(payload,model_version,prompt_version)
            store.db.execute('INSERT OR REPLACE INTO analysis_cache VALUES (?,?)',(key,json.dumps(result,ensure_ascii=False)))
            store.db.execute("UPDATE executions SET state='done' WHERE request_id=?",(rid,)); store.db.commit()
        except Exception: store.db.rollback(); raise
        return dict(result,cached=False)
