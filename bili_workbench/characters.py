"""Owner-created character profiles, isolated from the default character."""
import json
import re
import secrets
import time
from .runtime import Fault, canonical


class Characters:
    DEFAULT='default'
    def __init__(self,state):
        self.s=state
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_characters (id TEXT PRIMARY KEY,name TEXT NOT NULL,identity TEXT NOT NULL,traits TEXT NOT NULL,created REAL NOT NULL)')
        if 'archived' not in {row['name'] for row in self.s.db.execute('PRAGMA table_info(local_characters)')}:
            self.s.db.execute('ALTER TABLE local_characters ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
        self.s.db.execute('CREATE TABLE IF NOT EXISTS active_local_character (id INTEGER PRIMARY KEY CHECK(id=1),character_id TEXT NOT NULL)')
        self.s.db.execute("INSERT OR IGNORE INTO active_local_character VALUES(1,'default')")

    def list(self):
        with self.s.lock:
            active=self.s.db.execute('SELECT character_id FROM active_local_character WHERE id=1').fetchone()[0]
            rows=self.s.db.execute('SELECT id,name,identity,traits,archived FROM local_characters ORDER BY created,id').fetchall()
        return {'active':active,'characters':[{'id':'default','name':'大肥鱼','identity':'当前已保存的人格','traits':[],'archived':False}] + [dict(id=r['id'],name=r['name'],identity=r['identity'],traits=json.loads(r['traits']),archived=bool(r['archived'])) for r in rows]}

    def create(self,name,identity,traits):
        if not isinstance(name,str) or not name.strip() or len(name)>40:raise Fault('角色名称须为 1 至 40 字')
        if not isinstance(identity,str) or not identity.strip() or len(identity)>600:raise Fault('角色设定须为 1 至 600 字')
        if not isinstance(traits,list) or not 1<=len(traits)<=8 or any(not isinstance(x,str) or not x.strip() or len(x)>60 for x in traits):raise Fault('请填写 1 至 8 项性格，每项不超过 60 字')
        if any(ord(ch)<32 and ch not in '\n\t' for ch in name+identity+''.join(traits)):raise Fault('角色设定含无效字符')
        with self.s.lock:
            if self.s.db.execute('SELECT COUNT(*) FROM local_characters').fetchone()[0]>=20:raise Fault('角色数量已达上限')
            item_id=secrets.token_hex(12)
            self.s.db.execute('INSERT INTO local_characters(id,name,identity,traits,created) VALUES (?,?,?,?,?)',(item_id,name.strip(),identity.strip(),canonical([x.strip() for x in traits]),time.time()))
        return {'id':item_id,'name':name.strip()}

    def select(self,item_id):
        if item_id!='default' and (not isinstance(item_id,str) or not re.fullmatch(r'[0-9a-f]{24}',item_id)):raise Fault('角色编号无效')
        with self.s.lock:
            if item_id!='default' and not self.s.db.execute('SELECT 1 FROM local_characters WHERE id=? AND archived=0',(item_id,)).fetchone():raise Fault('角色不存在或已归档，请先恢复')
            self.s.db.execute('UPDATE active_local_character SET character_id=? WHERE id=1',(item_id,))
        return self.current()

    def current(self):
        listing=self.list()
        return next((x for x in listing['characters'] if x['id']==listing['active']),listing['characters'][0])

    def delete(self,item_id):
        if not isinstance(item_id,str) or not re.fullmatch(r'[0-9a-f]{24}',item_id):raise Fault('不能删除默认角色')
        with self.s.lock:
            if self.current()['id']==item_id:raise Fault('请先切换到其他角色')
            if not self.s.db.execute('UPDATE local_characters SET archived=1 WHERE id=? AND archived=0',(item_id,)).rowcount:raise Fault('角色不存在或已归档')
        return {'deleted':True,'archived':True,'note':'角色已归档，设定和历史仍保留，可以恢复。'}

    def restore(self,item_id):
        if not isinstance(item_id,str) or not re.fullmatch(r'[0-9a-f]{24}',item_id):raise Fault('角色编号无效')
        with self.s.lock:
            if not self.s.db.execute('UPDATE local_characters SET archived=0 WHERE id=? AND archived=1',(item_id,)).rowcount:raise Fault('角色不存在或未归档')
        return {'restored':True}
