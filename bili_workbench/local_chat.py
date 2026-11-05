"""Explicit owner-only local chat; no platform sends or automatic memory creation."""
import json,re,time
from .runtime import Fault,digest
from .social import validate_reply
from .growth import Scope

class LocalChat:
    def __init__(self,workbench):
        self.w=workbench
        self.w.s.db.execute('CREATE TABLE IF NOT EXISTS local_chat_requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL)')
        self.w.s.db.execute("UPDATE local_chat_requests SET state='uncertain' WHERE state='running'")
        self.w.s.db.execute("CREATE TABLE IF NOT EXISTS local_memory_requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,state TEXT NOT NULL,memory_id INTEGER)")
        self.w.s.db.execute("UPDATE local_memory_requests SET state='uncertain' WHERE state='running'")
        self.w.s.db.execute('CREATE TABLE IF NOT EXISTS local_sticker_messages(request_id TEXT PRIMARY KEY,sticker_id TEXT NOT NULL,sticker_name TEXT NOT NULL,character_id TEXT NOT NULL,turn_id INTEGER UNIQUE,state TEXT NOT NULL,created REAL NOT NULL)')
        self.w.s.db.execute("UPDATE local_sticker_messages SET state='uncertain' WHERE state='running'")
        from .local_recovery import LocalRecovery
        self.recovery=LocalRecovery(workbench)
    def history(self):
        with self.w.s.lock:
            active=self.w.characters.current()
            prefix='local-owner:' if active['id']=='default' else 'local-role:'+active['id']+':'
            c=self.w.growth.context(Scope.owner(),include_memory=False,max_turns=12,source_prefix=prefix,include_turn_ids=True)
            for message in c['messages']:
                if message['role']!='user':continue
                row=self.w.s.db.execute("SELECT m.sticker_id,m.sticker_name,EXISTS(SELECT 1 FROM local_stickers s WHERE s.id=m.sticker_id) AS available FROM local_sticker_messages m WHERE m.turn_id=? AND m.character_id=? AND m.state='completed'",(message['id'],active['id'])).fetchone()
                if row:message['sticker']={'id':row['sticker_id'],'name':row['sticker_name'],'available':bool(row['available'])}
            for message in c['messages']:
                if message['role']=='assistant':
                    expression=self.w.s.db.execute('SELECT event_id FROM local_reply_expression WHERE turn_id=? AND character_id=?',(message['id'],active['id'])).fetchone()
                    if expression:message['expression']=self.w.owner_expression.metadata(expression['event_id'])
            return {'messages':c['messages'],'omitted_history':c['omitted_history'],'scope':'owner_private','character_name':active['name'],'character_id':active['id']}

    @staticmethod
    def _sticker_character(character_id):
        if not isinstance(character_id,str) or (character_id!='default' and not re.fullmatch(r'[0-9a-f]{24}',character_id)):raise Fault('表情目标角色无效')

    def send_sticker(self,request_id,sticker_id,character_id):
        """A user-only local turn. No model, platform send, or automatic memory."""
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id):raise Fault('表情请求编号无效')
        if not isinstance(sticker_id,str) or not re.fullmatch(r'[0-9a-f]{24}',sticker_id):raise Fault('表情编号无效')
        self._sticker_character(character_id)
        with self.w.s.lock:
            if self.w.characters.current()['id']!=character_id:raise Fault('当前角色已改变，请重新读取对话后再发送表情')
            old=self.w.s.db.execute('SELECT * FROM local_sticker_messages WHERE request_id=?',(request_id,)).fetchone()
            if old:
                if old['sticker_id']!=sticker_id or old['character_id']!=character_id:raise Fault('表情请求编号内容冲突')
                return {'status':'already_recorded','previous_state':old['state'],'turn_id':old['turn_id'],'automatic_retry':False,'model_called':False,'platform_sent':False}
            item=self.w.s.db.execute('SELECT id,name FROM local_stickers WHERE id=?',(sticker_id,)).fetchone()
            if not item:raise Fault('表情已删除或不存在，请重新读取表情列表')
            self.w.s.db.execute("INSERT INTO local_sticker_messages VALUES (?,?,?,?,NULL,'running',?)",(request_id,sticker_id,item['name'],character_id,time.time()))
            prefix='local-owner:' if character_id=='default' else 'local-role:'+character_id+':'
            try:
                turn_id=self.w.growth.record_turn(Scope.owner(),'local-sticker:'+request_id,'user','[表情：'+item['name']+']',prefix+'本机表情')
                self.w.s.db.execute("UPDATE local_sticker_messages SET turn_id=?,state='completed' WHERE request_id=?",(turn_id,request_id))
                return {'status':'completed','turn_id':turn_id,'character_id':character_id,'automatic_retry':False,'model_called':False,'platform_sent':False}
            except Exception:
                self.w.s.db.execute("UPDATE local_sticker_messages SET state='uncertain' WHERE request_id=?",(request_id,))
                raise

    def sticker_image(self,turn_id,character_id):
        self._sticker_character(character_id)
        if type(turn_id) is not int or not 0<turn_id<=9007199254740991:raise Fault('表情对话编号无效')
        with self.w.s.lock:
            if self.w.characters.current()['id']!=character_id:raise Fault('当前角色已改变，请重新读取对话')
            row=self.w.s.db.execute("SELECT * FROM local_sticker_messages WHERE turn_id=? AND character_id=? AND state='completed'",(turn_id,character_id)).fetchone()
            if not row:
                reply=self.w.s.db.execute('SELECT event_id FROM local_reply_expression WHERE turn_id=? AND character_id=?',(turn_id,character_id)).fetchone()
                if not reply:raise Fault('此角色没有这条表情对话')
                with self.w.growth._db() as db:valid=db.execute("SELECT 1 FROM growth_turns WHERE id=? AND role='assistant' AND deleted=0 AND event_id=?",(turn_id,reply['event_id'])).fetchone()
                if not valid:raise Fault('此回复已删除')
                expression=self.w.owner_expression.metadata(reply['event_id']);sticker=expression['sticker']
                if not sticker:raise Fault('此回复没有已选表情')
                result={'turn_id':turn_id,'character_id':character_id,**sticker}
                if sticker['available']:result['png_base64']=self.w.stickers.get(sticker['id'])['png_base64']
                return result
            prefix='local-owner:' if character_id=='default' else 'local-role:'+character_id+':'
            with self.w.growth._db() as db:
                valid=db.execute("SELECT 1 FROM growth_turns WHERE id=? AND kind='friend' AND peer='local-owner' AND role='user' AND deleted=0 AND event_id=? AND source=?",(turn_id,'local-sticker:'+row['request_id'],prefix+'本机表情')).fetchone()
            if not valid:raise Fault('表情对话已删除或不可用')
            item=self.w.s.db.execute('SELECT id FROM local_stickers WHERE id=?',(row['sticker_id'],)).fetchone()
            result={'turn_id':turn_id,'character_id':character_id,'id':row['sticker_id'],'name':row['sticker_name'],'available':bool(item)}
            if item:result['png_base64']=self.w.stickers.get(item['id'])['png_base64']
            return result
    def remember_turn(self,request_id,turn_id,content,confirmation):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id):raise Fault('记忆请求编号无效')
        if type(turn_id) is not int or turn_id<=0:raise Fault('对话编号无效')
        if confirmation!='确认保存为主人记忆':raise Fault('请先审阅并确认保存')
        if not isinstance(content,str) or not content.strip() or len(content)>1000 or len(content.encode('utf8'))>4000 or any(ord(x)<32 and x not in '\n\t' for x in content):raise Fault('记忆内容无效或超过1000字')
        with self.w.s.lock:
            if self.w.characters.current()['id']!='default':raise Fault('当前角色不能写入大肥鱼的主人记忆')
            with self.w.growth._db() as db:
                row=db.execute("SELECT id FROM growth_turns WHERE id=? AND kind='friend' AND peer='local-owner' AND role='user' AND deleted=0 AND substr(source,1,12)='local-owner:'",(turn_id,)).fetchone()
            if row is None:raise Fault('只能保存大肥鱼本机对话中尚未删除的主人原话')
            fingerprint=digest([turn_id,content])
            old=self.w.s.db.execute('SELECT fingerprint,state,memory_id FROM local_memory_requests WHERE id=?',(request_id,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise Fault('记忆请求编号内容冲突')
                return {'status':'already_recorded','previous_state':old['state'],'memory_id':old['memory_id'],'automatic_retry':False}
            self.w.s.db.execute("INSERT INTO local_memory_requests VALUES (?,?,'running',NULL)",(request_id,fingerprint))
            try:
                ident=self.w.growth.controls().remember(Scope.owner(),content,'主人审阅的本机对话 #'+str(turn_id),1.0)
                self.w.s.db.execute("UPDATE local_memory_requests SET state='completed',memory_id=? WHERE id=?",(ident,request_id))
                return {'status':'completed','memory_id':ident,'automatic_retry':False,'model_called':False}
            except Exception:
                self.w.s.db.execute("UPDATE local_memory_requests SET state='uncertain' WHERE id=?",(request_id,))
                raise
    def send(self,request_id,text,include_memory=False,include_knowledge=False,*,cancel_check=None,stream_control=None,expected_character=None):
        if cancel_check is not None and not callable(cancel_check):raise Fault('取消检查无效')
        if stream_control is not None:
            from .model_stream import StreamControl
            if not isinstance(stream_control,StreamControl):raise Fault('模型流控制无效')
        def cancelled():return (cancel_check is not None and cancel_check()) or (stream_control is not None and stream_control.cancelled())
        if cancelled():raise Fault('本次对话已取消，未发送模型请求')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id):raise Fault('对话请求编号无效')
        if not isinstance(text,str) or not text.strip() or len(text)>1000 or len(text.encode('utf8'))>4000 or any(ord(x)<32 and x not in '\n\t' for x in text):raise Fault('请输入不超过1000字的对话内容')
        if type(include_memory) is not bool:raise Fault('记忆使用选项无效')
        if type(include_knowledge) is not bool:raise Fault('知识库使用选项无效')
        if expected_character is not None:self._sticker_character(expected_character)
        c=self.w.s.config()
        if not c['paid_enabled'] or not c['daily_budget_micro'] or not c['request_cap_micro']:raise Fault('模型调用未启用或预算为零；没有发送请求')
        if self.w.cancel_event.is_set():raise Fault('任务已暂停；没有发送请求')
        with self.w.s.lock:
            if cancelled():raise Fault('本次对话已取消，未发送模型请求')
            active=self.w.characters.current()
            if expected_character is not None and active['id']!=expected_character:raise Fault('当前角色已改变，本次对话没有发送；请重新读取对话')
            if active['id']!='default' and include_memory:raise Fault('新角色暂不使用大肥鱼的私人记忆，请取消勾选')
            prefix='local-owner:' if active['id']=='default' else 'local-role:'+active['id']+':'
            key='local:'+request_id
            fingerprint=digest([text,include_memory,include_knowledge] if active['id']=='default' else [text,include_memory,include_knowledge,active['id']])
            row=self.w.s.db.execute('SELECT fingerprint,state FROM local_chat_requests WHERE id=?',(request_id,)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint:raise Fault('对话编号内容冲突，不予重放')
                return {'status':'already_recorded','previous_state':row['state'],'automatic_retry':False}
            self.w.s.db.execute("INSERT INTO local_chat_requests VALUES (?,?,'running',?)",(request_id,fingerprint,time.time()))
            self.w.s.db.execute('INSERT INTO local_character_requests VALUES (?,?)',(request_id,active['id']))
            try:
                knowledge=self.w.knowledge.retrieve(text) if include_knowledge else []
                from bigfish_persona import chat_context_budget,format_reply
                context_limit=chat_context_budget(c['max_input_tokens'],len(text.encode('utf8')),len(json.dumps(knowledge,ensure_ascii=False).encode('utf8')))
                context=self.w.growth.context(Scope.owner(),include_memory=include_memory,max_turns=12,max_bytes=context_limit,exclude_event_id=key,source_prefix=prefix,memory_query=text if include_memory else None)
                conversation={'audience':'owner_private','scope':'local-owner','provenance':'local-owner','memory_domain':'owner-private'}
                if active['id']=='default':conversation['night_sleep_nudge_allowed']=self.w.owner_expression.night_allowed()
                if active['id']!='default':
                    context['personality']={'version':1,'data':{'name':active['name'],'identity':active['identity'],'traits':active['traits']}}
                    conversation['character_kind']='custom'
                tone=self.w.relationship.snapshot()['tone']
                if tone!='跟随人设':conversation['owner_selected_tone']=tone
                simulated_tone=self.w.emotion.predict(active['id'],text)
                if tone=='跟随人设' and simulated_tone not in (None,'跟随人设'):conversation['simulated_tone']=simulated_tone
                extra={'knowledge':knowledge} if knowledge else {}
                if stream_control is not None:extra['_stream_control']=stream_control
                if cancelled():raise Fault('本次对话已取消，未发送模型请求')
                result=self.w.model.chat(text,key,context['personality'],context['memories'],conversation=conversation,history=context['messages'],private_uncached=True,**extra)
                if cancelled():raise Fault('对话已取消，不保存或朗读回复；已发请求费用以账本为准')
                answer=validate_reply(result['text'],public=False)
                if active['id']=='default':answer=self.w.owner_expression.filter_sleep(format_reply(answer))
                turns=self.w.growth.record_exchange(Scope.owner(),key,text,answer,prefix+'本机主人会话')
                expression=self.w.owner_expression.plan('local',key,text,answer,owner_private=active['id']=='default')
                if expression['eligible']:
                    self.w.s.db.execute('INSERT OR IGNORE INTO local_reply_expression VALUES (?,?,?)',(turns['assistant'],key,active['id']));self.w.owner_expression.commit(key,answer)
                if simulated_tone is not None:self.w.emotion.record(active['id'],simulated_tone)
                self.w.s.db.execute("UPDATE local_chat_requests SET state='completed' WHERE id=?",(request_id,))
                return {'status':'completed','reply':answer,'expression':expression,'memory_used':len(context['memories']),'knowledge_used':len(knowledge),'history_turns_used':len(context['messages']),'model_called':result.get('model_called',not result.get('cached',False)),'platform_sent':False,'automatic_retry':False}
            except Exception:
                charged=self.w.s.db.execute('SELECT 1 FROM charges WHERE id=?',('model:'+key,)).fetchone()
                self.w.s.db.execute('UPDATE local_chat_requests SET state=? WHERE id=?',('uncertain' if charged else 'blocked',request_id))
                raise
