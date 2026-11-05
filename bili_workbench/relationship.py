"""Owner-visible local interaction stages and explicitly selected tone."""
import json
from .runtime import Fault, canonical


class Relationship:
    TONES=('跟随人设','轻快','温柔','认真')
    def __init__(self,state,characters=None):
        self.s=state;self.characters=characters
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_relationship (id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')
        self.s.db.execute('INSERT OR IGNORE INTO local_relationship VALUES (1,?)',(canonical({'tone':'跟随人设'}),))
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_character_requests (request_id TEXT PRIMARY KEY,character_id TEXT NOT NULL)')

    def snapshot(self):
        with self.s.lock:
            value=json.loads(self.s.db.execute('SELECT settings FROM local_relationship WHERE id=1').fetchone()[0])
            character_id=self.characters.current()['id'] if self.characters else 'default'
            count=self.s.db.execute("SELECT COUNT(*) FROM local_chat_requests r LEFT JOIN local_character_requests c ON c.request_id=r.id WHERE r.state='completed' AND COALESCE(c.character_id,'default')=?",(character_id,)).fetchone()[0]
        stage='初识' if count<5 else ('熟悉' if count<20 else '默契')
        return {'tone':value['tone'],'stage':stage,'local_conversations':count,'basis':'阶段仅根据已完成的本机对话次数计算，不代表真实情感或记忆。'}

    def set_tone(self,tone):
        if not isinstance(tone,str) or tone not in self.TONES:raise Fault('语气选项无效')
        with self.s.lock:self.s.db.execute('UPDATE local_relationship SET settings=? WHERE id=1',(canonical({'tone':tone}),))
        return self.snapshot()
