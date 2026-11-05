"""Trusted local receive adapter. No QQ send API, model provider or tools.

Only the authenticated local Event receiver may feed this worker. OfflineQQ
remains fixture-only. QQ scopes never alias the prototype's local-owner scope.
"""
import hashlib, json, sqlite3
from core import Core
from boundary import Store
from qq_offline import MockModel

class LiveEventAdapter:
    def __init__(self, path, bot_id, audience):
        self.bot_id=int(bot_id); self.audience=audience
        self.core=Core(path); self.store=Store(path)
        self.db=sqlite3.connect(str(path),isolation_level=None)
        self.db.execute('CREATE TABLE IF NOT EXISTS qq_live_events(bot INTEGER,peer INTEGER,mid TEXT,digest TEXT,status TEXT,PRIMARY KEY(bot,peer,mid))')
        self.model=MockModel()
    def close(self):
        self.db.close(); self.store.close(); self.core.close()
    def receive(self,e):
        if e.get('post_type')!='message' or e.get('message_type')!='private' or int(e.get('self_id',0))!=self.bot_id: return {'status':'rejected','send':False}
        peer=e.get('user_id'); mid=e.get('message_id')
        if type(peer) is not int or peer<=0 or mid is None: return {'status':'rejected','send':False}
        mode=self.audience.get('mode')
        if mode not in ('allowlist','all-private') or (mode=='allowlist' and str(peer) not in list(map(str,self.audience.get('userIds',[])))): return {'status':'rejected','send':False}
        parts=e.get('message')
        if not isinstance(parts,list) or not parts or len(parts)>100: return {'status':'unsupported-message','send':False}
        if any(not isinstance(s,dict) or s.get('type')!='text' or not isinstance(s.get('data'),dict) or not isinstance(s['data'].get('text'),str) for s in parts): return {'status':'unsupported-message','send':False}
        text=''.join(s['data']['text'] for s in parts)
        if not text.strip() or len(text.encode())>16000: return {'status':'unsupported-message','send':False}
        digest=hashlib.sha256(text.encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT digest FROM qq_live_events WHERE bot=? AND peer=? AND mid=?',(self.bot_id,peer,str(mid))).fetchone()
            if old:
                self.db.commit(); return {'status':'duplicate' if old[0]==digest else 'conflict','send':False}
            self.db.execute('INSERT INTO qq_live_events VALUES (?,?,?,?,?)',(self.bot_id,peer,str(mid),digest,'started'));self.db.commit()
        except Exception:self.db.rollback();raise
        scope=f'qq:{self.bot_id}:{peer}'
        personality=self.core.personality()
        memories=self.store.memories('friend',scope)
        draft=self.model.reply(text,personality,memories)
        if not draft.startswith('【离线模拟回复】'): raise RuntimeError('MockModel contract changed')
        self.db.execute("UPDATE qq_live_events SET status='mock_draft' WHERE bot=? AND peer=? AND mid=?",(self.bot_id,peer,str(mid)))
        return {'status':'mock_draft','send':False,'model':'MockModel','paid_cost':0,'personality_version':personality['version'],'memory_count':len(memories),'bot_id':self.bot_id,'sender_id':peer,'message_id':str(mid)}

if __name__=='__main__':
    import sys
    policy=json.load(open(sys.argv[2],encoding='utf-8-sig'))
    adapter=LiveEventAdapter(sys.argv[1],policy['botId'],policy['audience'])
    try:
        for line in sys.stdin:
            try: result=adapter.receive(json.loads(line))
            except Exception: result={'status':'processing-error','send':False}
            print(json.dumps(result),flush=True)
    finally: adapter.close()
