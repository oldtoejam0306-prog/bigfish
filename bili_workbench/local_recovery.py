"""Read-only outcome checks. Acknowledging never changes billing or replays work."""
from contextlib import contextmanager
import re
import sqlite3
import time
from .runtime import Fault


class LocalRecovery:
    CONFIRMATIONS={'chat':'已核对本次对话结果和账单','memory':'已核对本次记忆保存结果'}
    def __init__(self,workbench):
        self.w=workbench
        self.w.s.db.execute('CREATE TABLE IF NOT EXISTS local_recovery_ack(kind TEXT NOT NULL,request_id TEXT NOT NULL,character_id TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(kind,request_id,character_id))')
        self.w.s.db.execute('CREATE INDEX IF NOT EXISTS local_chat_recovery_state ON local_chat_requests(state)')
        self.w.s.db.execute('CREATE INDEX IF NOT EXISTS local_memory_recovery_state ON local_memory_requests(state)')

    @staticmethod
    def validate(character,kind=None,request_id=None):
        if not isinstance(character,str) or (character!='default' and not re.fullmatch(r'[0-9a-f]{24}',character)):raise Fault('核对角色无效')
        if kind is not None and (not isinstance(kind,str) or kind not in LocalRecovery.CONFIRMATIONS):raise Fault('核对类型无效')
        if kind is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id)):raise Fault('核对请求编号无效')

    @contextmanager
    def reader(self,character=None):
        # Do not wait for the workbench lock held during a provider request.
        # mode=ro prevents missing files from silently creating a new database.
        uri=(self.w.s.root/'workbench.db').resolve().as_uri()+'?mode=ro'
        db=sqlite3.connect(uri,uri=True,timeout=0.3);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            row=db.execute('SELECT character_id FROM active_local_character WHERE id=1').fetchone()
            if not row or (character is not None and row['character_id']!=character):raise Fault('当前角色已改变，请重新读取对话后核对')
            yield db
        finally:db.close()

    def current(self):
        with self.reader() as db:
            character=db.execute('SELECT character_id FROM active_local_character WHERE id=1').fetchone()['character_id']
            self.validate(character)
            return {'character_id':character,'operation_replayed':False}

    def _status(self,db,character,kind,request_id):
        if kind=='memory':
            if character!='default':raise Fault('当前角色不能核对大肥鱼的主人记忆')
            row=db.execute('SELECT state,memory_id FROM local_memory_requests WHERE id=?',(request_id,)).fetchone()
        else:
            row=db.execute("SELECT r.state FROM local_chat_requests r LEFT JOIN local_character_requests c ON c.request_id=r.id WHERE r.id=? AND COALESCE(c.character_id,'default')=?",(request_id,character)).fetchone()
        state=row['state'] if row else 'not_found'
        if state not in ('not_found','running','completed','blocked','uncertain'):raise Fault('请求状态无法确认')
        reviewed=bool(db.execute('SELECT 1 FROM local_recovery_ack WHERE kind=? AND request_id=? AND character_id=?',(kind,request_id,character)).fetchone())
        result={'kind':kind,'request_id':request_id,'character_id':character,'state':state,'reviewed':reviewed,'automatic_retry':False,'operation_replayed':False}
        if kind=='memory' and row and state=='completed':result['memory_id']=row['memory_id']
        if kind=='chat':
            charge=db.execute('SELECT state FROM charges WHERE id=?',('model:local:'+request_id,)).fetchone()
            # A foreign-role ID must not expose even the billing metadata.
            result['billing_state']=charge['state'] if row and charge and charge['state'] in ('reserved','settled','uncertain') else ('not_recorded' if not row or not charge else 'unknown')
            if row and charge:result['billing_request_id']='model:local:'+request_id
        return result

    def status(self,character,kind,request_id):
        if kind is None:raise Fault('核对类型无效')
        self.validate(character,kind,request_id)
        with self.reader(character) as db:return self._status(db,character,kind,request_id)

    def pending(self,character):
        self.validate(character)
        with self.reader(character) as db:
            rows=db.execute("""SELECT kind,request_id FROM (
                SELECT 'chat' AS kind,r.id AS request_id,r.rowid AS position FROM local_chat_requests r
                LEFT JOIN local_character_requests c ON c.request_id=r.id
                WHERE r.state IN ('running','uncertain') AND COALESCE(c.character_id,'default')=?
                UNION ALL SELECT 'memory',id,rowid FROM local_memory_requests
                WHERE state IN ('running','uncertain') AND ?='default'
                ) r WHERE NOT EXISTS(SELECT 1 FROM local_recovery_ack a WHERE a.kind=r.kind AND a.request_id=r.request_id AND a.character_id=?)
                ORDER BY kind,position DESC LIMIT 21""",(character,character,character)).fetchall()
            return {'character_id':character,'items':[self._status(db,character,r['kind'],r['request_id']) for r in rows[:20]],'has_more':len(rows)>20,'automatic_retry':False}

    def acknowledge(self,character,kind,request_id,confirmation):
        if kind is None:raise Fault('核对类型无效')
        self.validate(character,kind,request_id)
        if confirmation!=self.CONFIRMATIONS[kind]:raise Fault('请先核对原操作结果；对话还需核对提供方账单')
        with self.w.s.lock:
            status=self.status(character,kind,request_id)
            if status['state'] in ('not_found','running'):raise Fault('此请求尚无可确认的结束状态，不能结束核对')
            self.w.s.db.execute('INSERT OR IGNORE INTO local_recovery_ack VALUES (?,?,?,?)',(kind,request_id,character,time.time()))
        return {'acknowledged':True,'kind':kind,'request_id':request_id,'character_id':character,'billing_changed':False,'operation_replayed':False}
