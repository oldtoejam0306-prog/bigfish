"""Scoped conversational growth using the existing Core and boundary.Store.

This module has no network, model, credential or send interface. Scope creation
is for an already trusted local adapter; it is not QQ identity authentication.
External text is recorded as material and never interpreted as a command.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3

from boundary import Store
from core import Core, INITIAL
from .runtime import Fault, canonical


def _text(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > maximum:
        raise Fault(name+'无效或过长')
    return value


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Scope:
    kind: str
    peer: str
    owner_private: bool = False

    def __post_init__(self):
        if self.kind not in ('friend', 'group') or type(self.owner_private) is not bool:
            raise Fault('会话作用域无效')
        _text(self.peer, '会话标识', 128)
        if '\x00' in self.peer or self.peer != self.peer.strip():raise Fault('会话标识无效')
        if self.owner_private != (self.kind == 'friend' and self.peer == 'local-owner'):
            raise Fault('主人私聊必须使用独立local-owner作用域')

    @classmethod
    def owner(cls):return cls('friend', 'local-owner', True)
    @classmethod
    def friend(cls, peer):return cls('friend', str(peer))
    @classmethod
    def group(cls, peer):return cls('group', str(peer))


def _scope(value):
    if type(value) is not Scope:raise Fault('需要可信适配层生成的Scope')
    return value


class Growth:
    def __init__(self, root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True, exist_ok=True)
        self.path=self.root/'growth.db';self.__capability=object()
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS growth_turns(
                id INTEGER PRIMARY KEY,kind TEXT NOT NULL,peer TEXT NOT NULL,
                event_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT,
                source TEXT NOT NULL,fingerprint TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL,UNIQUE(kind,peer,event_id,role));
            CREATE TABLE IF NOT EXISTS growth_turn_events(
                id INTEGER PRIMARY KEY,turn_id INTEGER NOT NULL,action TEXT NOT NULL,
                source TEXT NOT NULL,note TEXT NOT NULL,created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS growth_proposals(
                id INTEGER PRIMARY KEY,base_version INTEGER NOT NULL,data TEXT NOT NULL,
                instruction TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,
                after_version INTEGER,created TEXT NOT NULL);
            ''')
        if os.name!='nt':os.chmod(self.path,0o600)

    @contextmanager
    def _db(self):
        # Each call owns its connection in the current thread.
        db=sqlite3.connect(str(self.path),timeout=30,isolation_level=None)
        db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def close(self):
        """No persistent connection is held; provided for common service lifecycle."""

    def controls(self):
        """Only hand this capability to trusted local owner settings/review code."""
        return GrowthControls(self,self.__capability)

    def _memory_path(self,scope):
        return self.root/('character.db' if scope.owner_private else 'social-memory.db')

    def record_turn(self,scope,event_id,role,text,source):
        scope=_scope(scope);_text(event_id,'事件ID',160);_text(source,'来源',500)
        if role not in ('user','assistant'):raise Fault('只允许用户/角色文本回合')
        _text(text,'会话文本',16000)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                ident=self._insert_turn(db,scope,event_id,role,text,source)
                db.commit();return ident
            except Exception:db.rollback();raise

    def _insert_turn(self,db,scope,event_id,role,text,source):
        fingerprint=_hash(canonical([role,text,source]))
        row=db.execute('SELECT * FROM growth_turns WHERE kind=? AND peer=? AND event_id=? AND role=?',(scope.kind,scope.peer,event_id,role)).fetchone()
        if row:
            if row['fingerprint']!=fingerprint:raise Fault('会话事件ID内容冲突')
            if row['deleted']:raise Fault('已删除回合不可通过重放恢复')
            return row['id']
        return db.execute('INSERT INTO growth_turns(kind,peer,event_id,role,content,source,fingerprint,created) VALUES (?,?,?,?,?,?,?,?)',(scope.kind,scope.peer,event_id,role,text,source,fingerprint,_stamp())).lastrowid

    def record_exchange(self,scope,event_id,user_text,assistant_text,source):
        scope=_scope(scope);_text(event_id,'事件ID',160);_text(source,'来源',500)
        _text(user_text,'用户文本',16000);_text(assistant_text,'角色文本',16000)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                values={role:self._insert_turn(db,scope,event_id,role,text,source) for role,text in (('user',user_text),('assistant',assistant_text))}
                db.commit();return values
            except Exception:db.rollback();raise

    def context(self,scope,public_persona=None,include_memory=False,max_turns=12,max_bytes=5500,exclude_event_id=None,source_prefix=None,include_turn_ids=False,memory_query=None):
        scope=_scope(scope)
        if type(include_turn_ids) is not bool:raise Fault('历史编号选项无效')
        if memory_query is not None:
            if not scope.owner_private or not include_memory:raise Fault('记忆检索仅用于明确选择的主人私聊')
            _text(memory_query,'记忆检索内容',4000)
        if type(include_memory) is not bool or type(max_turns) is not int or not 0<=max_turns<=40 or type(max_bytes) is not int or not 1000<=max_bytes<=6000:raise Fault('上下文限制无效')
        if exclude_event_id is not None:_text(exclude_event_id,'排除事件ID',160)
        # Fixed provenance classes, never a model-controlled SQL wildcard.
        if source_prefix is not None and source_prefix not in ('fixture:','qq-authenticated:','local-owner:') and not re.fullmatch(r'local-role:[0-9a-f]{24}:',source_prefix):raise Fault('历史来源前缀无效')
        if scope.owner_private:
            core=Core(self.root/'character.db')
            try:personality=core.personality()
            finally:core.close()
        else:
            _text(public_persona,'公开人格',2000)
            personality={'version':1,'data':{'identity':'AI虚拟角色，不声称真实生命或意识','public_profile':public_persona}}
        result={'personality':personality,'messages':[],'memories':[],'scope':{'kind':scope.kind,'peer':scope.peer,'owner_private':scope.owner_private},'history_selected':0,'memories_selected':0,'omitted_history':0,'omitted_memories':0}
        if len(canonical(result).encode())>max_bytes:raise Fault('人格超出上下文字节上限，需要主人修正')
        if include_memory:
            memory=Store(self._memory_path(scope))
            try:
                memory_total=memory.db.execute('SELECT count(*) FROM scoped_memory WHERE kind=? AND peer=? AND deleted=0',(scope.kind,scope.peer)).fetchone()[0]
                rows=memory.db.execute('SELECT id,layer,content,source,confidence FROM scoped_memory WHERE kind=? AND peer=? AND deleted=0 ORDER BY id DESC LIMIT 101',(scope.kind,scope.peer)).fetchall()
            finally:memory.close()
            if memory_query is not None:
                query=''.join(ch for ch in memory_query.casefold() if ch.isalnum())
                stop={'我们','你我','我的','什么','记得','我想','是否','是不是'}
                terms={query[i:i+2] for i in range(len(query)-1) if query[i:i+2] not in stop}
                if not terms and len(query)==1:terms={query}
                if terms:
                    def relevance(row):
                        content=''.join(ch for ch in row['content'].casefold() if ch.isalnum())
                        return (sum(term in content for term in terms),row['id'])
                    rows.sort(key=relevance,reverse=True)
            for row in rows:
                item=dict(row)
                if len(result['memories'])<10:
                    result['memories'].insert(0,item)
                    if len(canonical(result).encode())<=max_bytes-200:continue
                    result['memories'].pop(0)
            result['omitted_memories']=memory_total-len(result['memories'])
        with self._db() as db:
            history_where='kind=? AND peer=? AND deleted=0 AND (? IS NULL OR event_id!=?) AND (? IS NULL OR substr(source,1,length(?))=?)'
            history_args=(scope.kind,scope.peer,exclude_event_id,exclude_event_id,source_prefix,source_prefix,source_prefix)
            history_total=db.execute('SELECT count(*) FROM growth_turns WHERE '+history_where,history_args).fetchone()[0]
            rows=db.execute('SELECT id,event_id,role,content,source FROM growth_turns WHERE '+history_where+' ORDER BY id DESC LIMIT 101',history_args).fetchall()
        for row in rows:
            if row['event_id']==exclude_event_id:continue
            item={'role':row['role'],'content':row['content']}
            if include_turn_ids:item['id']=row['id']
            if len(result['messages'])<max_turns:
                result['messages'].insert(0,item)
                if len(canonical(result).encode())<=max_bytes-100:continue
                result['messages'].pop(0)
        result['omitted_history']=history_total-len(result['messages'])
        result['history_selected']=len(result['messages']);result['memories_selected']=len(result['memories'])
        # Counts can grow by a few digits after selection; the reserved margin
        # leaves the complete serializable context within the requested cap.
        if len(canonical(result).encode())>max_bytes:raise Fault('上下文超过上限')
        return result


class GrowthControls:
    def __init__(self,growth,capability):
        if capability is not growth._Growth__capability:raise PermissionError('trusted local control required')
        self.g=growth

    def _public_approval(self,scope,public_approved):
        scope=_scope(scope)
        if type(public_approved) is not bool or (not scope.owner_private and not public_approved):raise Fault('公开域记忆须主人明确批准，不能自动复制私人资料')
        return scope

    def list_memories(self,scope,before_id=None):
        scope=_scope(scope)
        if before_id is not None and (type(before_id) is not int or before_id<=0):raise Fault('记忆分页编号无效')
        memory=Store(self.g._memory_path(scope))
        try:
            rows=memory.db.execute('SELECT id,layer,content,source,confidence FROM scoped_memory WHERE kind=? AND peer=? AND deleted=0 AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21',(scope.kind,scope.peer,before_id,before_id)).fetchall()
        finally:memory.close()
        page=rows[:20];more=len(rows)>20
        return {'memories':[dict(row) for row in page],'has_more':more,'next_before_id':page[-1]['id'] if more else None,'scope':{'kind':scope.kind,'peer':scope.peer}}

    def remember(self,scope,content,source,confidence,layer='long',*,public_approved=False):
        scope=self._public_approval(scope,public_approved)
        _text(content,'记忆',12000);_text(source,'来源',500)
        if type(confidence) not in (float,int) or not math.isfinite(confidence) or not 0<=confidence<=1:raise Fault('置信度无效')
        memory=Store(self.g._memory_path(scope))
        try:return memory.add_memory(scope.kind,scope.peer,layer,content,source,float(confidence))
        finally:memory.close()

    def correct_memory(self,scope,ident,content,source,*,public_approved=False):
        scope=self._public_approval(scope,public_approved)
        _text(content,'记忆',12000);_text(source,'来源',500)
        if type(ident) is not int or ident<=0:raise Fault('记忆ID无效')
        memory=Store(self.g._memory_path(scope))
        try:memory.change_memory(scope.kind,scope.peer,ident,source,content)
        finally:memory.close()
        return {'id':ident,'corrected':True}

    def delete_memory(self,scope,ident,source):
        scope=_scope(scope);_text(source,'来源',500)
        if type(ident) is not int or ident<=0:raise Fault('记忆ID无效')
        memory=Store(self.g._memory_path(scope))
        try:memory.change_memory(scope.kind,scope.peer,ident,source)
        finally:memory.close()
        return {'id':ident,'deleted':True,'erasure':'逻辑删除并清空当前原文；不是存储介质安全擦除'}

    def _turn_change(self,scope,ident,source,new_content):
        scope=_scope(scope);_text(source,'来源',500)
        if type(ident) is not int or ident<=0:raise Fault('回合ID无效')
        if new_content is not None:_text(new_content,'会话文本',16000)
        with self.g._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM growth_turns WHERE id=? AND kind=? AND peer=? AND deleted=0',(ident,scope.kind,scope.peer)).fetchone()
                if row is None:raise KeyError(ident)
                action='deleted' if new_content is None else 'corrected'
                db.execute('UPDATE growth_turns SET content=?,deleted=? WHERE id=?',(new_content,int(new_content is None),ident))
                # Fingerprint remains the original immutable ingress binding so
                # a duplicate cannot overwrite or resurrect this correction.
                db.execute('INSERT INTO growth_turn_events(turn_id,action,source,note,created) VALUES (?,?,?,?,?)',(ident,action,source,'previous_sha256:'+_hash(row['content']),_stamp()))
                db.commit()
            except Exception:db.rollback();raise
        return {'id':ident,'action':action}

    def correct_turn(self,scope,ident,content,source):return self._turn_change(scope,ident,source,content)
    def delete_turn(self,scope,ident,source):return self._turn_change(scope,ident,source,None)

    def delete_exchange(self,scope,event_id,source):
        scope=_scope(scope);_text(event_id,'事件ID',160);_text(source,'来源',500)
        with self.g._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                rows=db.execute('SELECT id,content FROM growth_turns WHERE kind=? AND peer=? AND event_id=? AND deleted=0',(scope.kind,scope.peer,event_id)).fetchall()
                if not rows:raise KeyError(event_id)
                for row in rows:
                    db.execute('UPDATE growth_turns SET content=NULL,deleted=1 WHERE id=?',(row['id'],))
                    db.execute('INSERT INTO growth_turn_events(turn_id,action,source,note,created) VALUES (?,?,?,?,?)',(row['id'],'deleted',source,'previous_sha256:'+_hash(row['content']),_stamp()))
                db.commit()
            except Exception:db.rollback();raise
        return {'deleted_turns':len(rows),'erasure':'逻辑删除，不是安全擦除'}

    def _personality(self,data):
        if not isinstance(data,dict) or set(data)!=set(INITIAL) or len(canonical(data).encode())>4000:raise Fault('人格须为有界完整结构')
        if not isinstance(data['traits'],list) or not 1<=len(data['traits'])<=20 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in data['traits']):raise Fault('人格特征无效')
        for key in ('identity','creative_interest'):_text(data[key],key,2000)

    def correct_personality(self,instruction,data,source):
        self._personality(data);_text(instruction,'纠正说明',1000);_text(source,'来源',500)
        core=Core(self.g.root/'character.db')
        try:return core.correct(instruction+' [source:'+source+']',data)
        finally:core.close()

    def propose_growth(self,instruction,data,source):
        self._personality(data);_text(instruction,'成长说明',1000);_text(source,'来源',500)
        core=Core(self.g.root/'character.db')
        try:version=core.personality()['version']
        finally:core.close()
        with self.g._db() as db:
            ident=db.execute("INSERT INTO growth_proposals(base_version,data,instruction,source,state,created) VALUES (?,?,?,?,'pending',?)",(version,canonical(data),instruction,source,_stamp())).lastrowid
        return {'id':ident,'state':'pending','base_version':version,'automatically_applied':False}

    def pending_growth(self,before_id=None):
        """Return bounded local-owner review material, without applying it."""
        if before_id is not None and (type(before_id) is not int or before_id<=0):raise Fault('成长提案翻页标识无效')
        with self.g._db() as db:
            rows=db.execute("SELECT id,base_version,data,instruction,source,created FROM growth_proposals WHERE state='pending' AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT 21",(before_id,before_id)).fetchall()
            more=len(rows)>20;rows=rows[:20]
            return {'proposals':[{'id':r['id'],'base_version':r['base_version'],'personality':json.loads(r['data']),'instruction':r['instruction'],'source':r['source'],'created':r['created']} for r in rows], 'has_more':more,'next_before_id':rows[-1]['id'] if more else None,'automatically_applied':False}

    def reject_growth(self,ident):
        if type(ident) is not int or ident<=0:raise Fault('成长提案ID无效')
        with self.g._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT state FROM growth_proposals WHERE id=?',(ident,)).fetchone()
                if not row:raise Fault('成长提案不存在')
                if row['state']=='rejected':db.commit();return {'id':ident,'state':'rejected'}
                if row['state']!='pending':raise Fault('仅待审提案可以放弃')
                db.execute("UPDATE growth_proposals SET state='rejected' WHERE id=?",(ident,));db.commit()
                return {'id':ident,'state':'rejected'}
            except Exception:db.rollback();raise

    def apply_growth(self,ident):
        if type(ident) is not int or ident<=0:raise Fault('成长提案ID无效')
        with self.g._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM growth_proposals WHERE id=?',(ident,)).fetchone()
                if not row:raise KeyError(ident)
                if row['state']=='applied':db.commit();return row['after_version']
                if row['state']!='pending':raise Fault('成长应用结果未知，禁止自动重放')
                core=Core(self.g.root/'character.db')
                try:current=core.personality()['version']
                finally:core.close()
                if current!=row['base_version']:raise Fault('人格已更新，请重新审阅成长提案')
                db.execute("UPDATE growth_proposals SET state='applying' WHERE id=?",(ident,));db.commit()
            except Exception:db.rollback();raise
        # Deliberately do not auto-retry after a crash between the two stores.
        core=Core(self.g.root/'character.db')
        try:
            # Core's transaction must cover the optimistic version check too;
            # callers outside Growth may concurrently use Core.correct().
            core.db.execute('BEGIN IMMEDIATE')
            old=core.personality()['version']
            if old!=row['base_version']:
                core.db.rollback()
                with self.g._db() as db:db.execute("UPDATE growth_proposals SET state='stale' WHERE id=?",(ident,))
                raise Fault('人格已更新，请重新审阅成长提案')
            version=old+1;instruction=row['instruction']+' [source:'+row['source']+']';created=_stamp()
            # This is the existing Core schema, not a second personality store.
            core.db.execute('INSERT INTO personality VALUES (?,?,?,?)',(version,row['data'],instruction,created))
            core.db.execute('INSERT INTO corrections(before_version,after_version,instruction,created) VALUES (?,?,?,?)',(old,version,instruction,created));core.db.commit()
        except Exception:
            core.db.rollback();raise
        finally:core.close()
        with self.g._db() as db:db.execute("UPDATE growth_proposals SET state='applied',after_version=? WHERE id=?",(version,ident))
        return version
