"""Configurable QQ social/model glue. Fixture ingress only; no socket, login or sender.
A live bridge must authenticate its transport separately before any integration.
"""
import argparse
import json
import time
from pathlib import Path
from .runtime import Fault,canonical,digest
from qq_offline import OfflineConfig,Rejected
from .social import SOCIAL_DEFAULTS,validate_policy,parse_fixture,validate_reply

DEFAULT_QQ={'enabled':False,'bot_id':None,'owner_id':None,'include_owner_memory':False,'include_public_memory':False,'daily_limit':20,'interval_s':3,'acceptance_owner_only':False,**SOCIAL_DEFAULTS}

class QQGlue:
    def __init__(self,w):
        self.w=w;db=w.s.db
        db.executescript('''CREATE TABLE IF NOT EXISTS qq_settings(id INTEGER PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS qq_identity(id INTEGER PRIMARY KEY,bot INTEGER,owner INTEGER);
        CREATE TABLE IF NOT EXISTS qq_model_events(key TEXT PRIMARY KEY,digest TEXT,state TEXT,result TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS qq_model_attempts(id INTEGER PRIMARY KEY,event_key TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS qq_deferred_inputs(key TEXT PRIMARY KEY,event TEXT,scope TEXT,resume_at TEXT,created REAL);
        ''')
        if 'provenance' not in [r[1] for r in db.execute('PRAGMA table_info(qq_deferred_inputs)')]:db.execute("ALTER TABLE qq_deferred_inputs ADD COLUMN provenance TEXT NOT NULL DEFAULT 'fixture'")
        if 'scope' not in [r[1] for r in db.execute('PRAGMA table_info(qq_model_attempts)')]:db.execute("ALTER TABLE qq_model_attempts ADD COLUMN scope TEXT NOT NULL DEFAULT 'legacy'")
        db.execute('INSERT OR IGNORE INTO qq_settings VALUES (1,?)',(canonical(DEFAULT_QQ),))
        db.execute("UPDATE qq_model_events SET state='uncertain' WHERE state IN ('running','running_local')")
    def config(self):return dict(json.loads(canonical(DEFAULT_QQ)),**json.loads(self.w.s.db.execute('SELECT value FROM qq_settings WHERE id=1').fetchone()[0]))
    def configure(self,values):
        if not isinstance(values,dict) or set(values)-set(DEFAULT_QQ):raise Fault('未知QQ设置')
        with self.w.s.lock:
            c=dict(self.config(),**values)
            if any(type(c[field]) is not bool for field in ('enabled','include_owner_memory','include_public_memory','acceptance_owner_only')):raise Fault('QQ开关须为布尔值')
            if type(c['daily_limit']) is not int or not 0<=c['daily_limit']<=100 or type(c['interval_s']) is not int or not 1<=c['interval_s']<=3600:raise Fault('QQ频率/次数无效')
            validate_policy(c)
            for field in ('friend_allowlist','group_allowlist','blocked_users','blocked_groups'):c[field]=list(dict.fromkeys(str(int(x)) for x in c[field]))
            try:OfflineConfig(c['bot_id'],c['owner_id']).validate()
            except Rejected:raise Fault('QQ身份无效') from None
            if c['enabled'] and (c['bot_id'] is None or c['owner_id'] is None):raise Fault('先绑定独立机器人和主人身份')
            if c['bot_id'] is not None and c['owner_id'] is not None:
                old=self.w.s.db.execute('SELECT bot,owner FROM qq_identity WHERE id=1').fetchone()
                if old and tuple(old)!=(c['bot_id'],c['owner_id']):raise Fault('此目录已绑定QQ身份，不可改为他人；须审查数据迁移')
                self.w.s.db.execute('INSERT OR IGNORE INTO qq_identity VALUES (1,?,?)',(c['bot_id'],c['owner_id']))
            elif self.w.s.db.execute('SELECT 1 FROM qq_identity').fetchone():raise Fault('不能清空已绑定身份；可关闭QQ开关')
            self.w.s.db.execute('UPDATE qq_settings SET value=? WHERE id=1',(canonical(c),))
            return c
    def check(self):
        c=self.config();m=self.w.s.config();v=self.w.s.vault.read();missing=[]
        for ok,label in [(c['enabled'],'QQ草稿能力开关'),(c['bot_id'] is not None and c['owner_id'] is not None,'独立机器人和主人QQ身份'),(bool(m['provider_url'] and m['model'] and v.get('api_key')),'模型地址/模型名/本机Key'),(bool(m['paid_enabled'] and m['daily_budget_micro'] and m['request_cap_micro']),'共享付费授权与预算'),(bool(m['price_version'] and m['input_price_micro_per_million'] and m['output_price_micro_per_million']),'真实计价版本/单价')]:
            if not ok:missing.append(label)
        if self.w.s.db.execute("SELECT 1 FROM charges WHERE state IN ('reserved','uncertain')").fetchone():missing.append('先核对未知费用')
        return {'fixture_model_ready':not missing,'missing':missing,'live_qq_ready':False,'ingress':'fixture与验签HTTP分开；HMAC仅证明桥传输，不是独立QQ平台身份凭证','sender_implemented':True,'sender_protocol':'OneBot v11 HTTP候选，实际SnowLuma仍待验收','social_authorization':{'mode':c['social_mode'],'groups_enabled':c['groups_enabled'] and c['social_mode']!='owner_only' and c['group_reply_mode']!='off','other_friends_enabled':c['social_mode']!='owner_only','public_context_only':True},'acceptance_profile':{'owner_only':c['acceptance_owner_only'],'does_not_revoke_social_policy':True},'transport_gate':{'validated':False,'automatic_send_enabled':False,'reason':'真实传输/回执尚未验收；不撤销社交授权'},'budget':'与B站共用workbench.db账本，不各自重复额度','requires_live_test':['SnowLuma真实消息协议/传输认证','回执与重连语义','主人及独立机器人账号映射','真实推理与费用'],'autonomous_social_authorized':c['social_mode']!='owner_only','automatic_social_send_ready':False}
    def _growth_scope(self,parsed):
        from .growth import Scope
        return Scope.owner() if parsed.owner_private else (Scope.friend(str(parsed.peer)) if parsed.kind=='friend' else Scope.group(str(parsed.peer)))
    def _context(self,c,parsed,event_key,authenticated=False):
        from bigfish_persona import chat_context_budget
        maximum=chat_context_budget(self.w.s.config()['max_input_tokens'],len(parsed.text.encode()))
        return self.w.growth.context(self._growth_scope(parsed),public_persona=c['public_persona'],include_memory=c['include_owner_memory'] if parsed.owner_private else c['include_public_memory'],max_turns=12,max_bytes=maximum,exclude_event_id=event_key,source_prefix='qq-authenticated:' if authenticated else 'fixture:')
    def _park(self,parsed,event,key,fingerprint,scope,wait,authenticated=False):
        c=self.config();db=self.w.s.db
        existing=db.execute('SELECT 1 FROM qq_deferred_inputs WHERE key=?',(key,)).fetchone()
        if not existing:
            if db.execute('SELECT count(*) FROM qq_deferred_inputs').fetchone()[0]>=min(100,c['daily_limit']):raise Fault('低谷待办已达上限')
            if db.execute('SELECT count(*) FROM qq_deferred_inputs WHERE scope=?',(scope,)).fetchone()[0]>=c['per_peer_daily_limit']:raise Fault('此会话低谷待办已达上限')
        # Only validated event fields are persisted, never arbitrary transport metadata.
        clean={'self_id':parsed.bot,'user_id':parsed.sender,'message_id':parsed.message_id,'post_type':'message','message_type':'private' if parsed.kind=='friend' else 'group','sub_type':'friend' if parsed.kind=='friend' else 'normal','message':[{'type':'text','data':{'text':parsed.text}}]}
        if parsed.kind=='group':
            clean['group_id']=parsed.peer
            clean['message'] += [{'type':'at','data':{'qq':str(x['data']['qq'])}} for x in event['message'] if x['type']=='at']
        db.execute("INSERT INTO qq_model_events VALUES (?,?,'deferred',NULL,?) ON CONFLICT(key) DO UPDATE SET state='deferred'",(key,fingerprint,time.time()))
        db.execute('INSERT INTO qq_deferred_inputs(key,event,scope,resume_at,created,provenance) VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET resume_at=excluded.resume_at',(key,canonical(clean),scope,wait.detail['resume_at_utc'],time.time(),'qq-authenticated' if authenticated else 'fixture'))
        return {'status':'deferred','event_key':key,'resume_at_utc':wait.detail['resume_at_utc'],'send':False,'fixture':not authenticated,'platform_authenticated':False,'transport_authenticated':authenticated,'reason':wait.detail['defer_reason'],'note':'未调用模型；待办仅存本机，恢复仍核验身份/预算/未决费用'}
    def resume_deferred(self,max_jobs=1,*,authenticated_only=False):
        if type(max_jobs) is not int or not 1<=max_jobs<=3 or type(authenticated_only) is not bool:raise Fault('恢复参数无效，每次最多3个待办')
        from .deepseek_test import require_window,OffPeakDeferred
        try:require_window(self.w.s.config()['provider_url'])
        except OffPeakDeferred as wait:return {'waiting':True,'resume_at_utc':wait.detail['resume_at_utc'],'results':[]}
        with self.w.s.lock:
            rows=self.w.s.db.execute("SELECT event,provenance FROM qq_deferred_inputs WHERE (?=0 OR provenance='qq-authenticated') ORDER BY created LIMIT ?",(int(authenticated_only),max_jobs)).fetchall()
            return {'waiting':False,'results':[self._process_message(json.loads(r['event']),authenticated=r['provenance']=='qq-authenticated') for r in rows],'sent':False,'execution':'model_drafts_only'}
    def _owner_command(self,parsed,key,authenticated):
        if not authenticated or not parsed.owner_private or not parsed.text.startswith('/') :return None
        text=parsed.text;control=self.w.growth.controls();scope=self._growth_scope(parsed);answer=None
        import re
        if re.search(r'\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,})\b',text):raise Fault('不要在聊天里保存凭据，请仅使用本机密码框')
        if text.startswith('/记住 '):
            ident=control.remember(scope,text[4:].strip(),'qq-authenticated:主人私聊明确要求保存',0.9);answer='已作为有来源的主人私聊记忆保存，编号'+str(ident)+'。'
        elif text.startswith('/纠正记忆 '):
            fields=text.split(' ',2)
            if len(fields)!=3 or not fields[1].isdigit():raise Fault('格式：/纠正记忆 编号 新内容')
            control.correct_memory(scope,int(fields[1]),fields[2],'qq-authenticated:主人明确纠正');answer='已纠正该条私聊记忆。'
        elif text.startswith('/忘记记忆 '):
            number=text[len('/忘记记忆 '):].strip()
            if not number.isdigit():raise Fault('格式：/忘记记忆 编号')
            control.delete_memory(scope,int(number),'qq-authenticated:主人明确删除');answer='已逻辑删除该条记忆，不代表介质安全擦除。'
        elif text=='/暂停自动交流':self.w.interaction.pause();answer='已暂停后台处理与自动发送，不会继续调用模型。'
        elif text=='/解除暂停':self.w.cancel_event.clear();answer='已解除取消状态；付费、后台和发送开关未自动开启，额度未增加。'
        if answer is None:return None
        candidate={'action':'send_private_msg','params':{'user_id':parsed.sender,'message':[{'type':'text','data':{'text':answer}}]},'echo':key}
        return {'status':'model_draft','event_key':key,'reply_candidate':candidate,'model':None,'model_called':False,'send':False,'fixture':False,'platform_authenticated':False,'transport_authenticated':True,'local_owner_command':True,'audience':'owner_private'}
    def process_fixture(self,event,*,retry_blocked=False):
        return self._process_message(event,retry_blocked=retry_blocked,authenticated=False)
    def process_authenticated(self,transport,trusted_event):
        from .qq_transport import OneBotHTTP
        if type(transport) is not OneBotHTTP:raise Fault('须使用已验签的OneBot适配器')
        event=transport.event_dict(trusted_event)
        return self._process_message(event,authenticated=True)
    def _process_message(self,event,*,retry_blocked=False,authenticated=False):
        """Internal trusted dispatch; not directly exposed to network.
        A local fixture call is never a trusted QQ authentication assertion.
        Real model code may run ONLY if the owner has enabled paid configuration.
        No text commands, source fields or model output can mutate configuration.
        """
        with self.w.s.lock:
            c=self.config()
            if not c['enabled']:raise Fault('QQ能力未启用，默认拒绝')
            if type(retry_blocked) is not bool:raise Fault('重试标识无效')
            parsed=parse_fixture(event,c);text=parsed.text
            if c['acceptance_owner_only'] and not parsed.owner_private:raise Fault('当前仅进行主人私聊模型验收，日常社交策略保留，暂不处理其他会话')
            # Preserve existing owner-private keys; other scopes never share them.
            parts=[parsed.bot,parsed.sender,parsed.message_id] if parsed.owner_private else [parsed.bot,parsed.kind,parsed.peer,parsed.sender,parsed.message_id]
            if authenticated:parts=['qq-authenticated',parsed.bot,parsed.kind,parsed.peer,parsed.sender,parsed.message_id]
            key='qq:'+digest(parts);fingerprint=digest(text)
            scope=digest([parsed.bot,parsed.kind,parsed.peer])
            row=self.w.s.db.execute('SELECT * FROM qq_model_events WHERE key=?',(key,)).fetchone()
            if row:
                if row['digest']!=fingerprint:raise Fault('消息ID内容冲突，禁止覆盖或重放')
                if row['state']!='deferred' and (row['state']!='blocked' or not retry_blocked):return {'status':'duplicate_or_uncertain','event_key':key,'previous_state':row['state'],'send':False,'fixture':not authenticated}
            is_command=authenticated and parsed.owner_private and (parsed.text.startswith(('/记住 ','/纠正记忆 ','/忘记记忆 ')) or parsed.text in ('/暂停自动交流','/解除暂停'))
            if is_command:self.w.s.db.execute("INSERT INTO qq_model_events VALUES (?,?,'running_local',NULL,?)",(key,fingerprint,time.time()))
            command=self._owner_command(parsed,key,authenticated)
            if command is not None:
                self.w.s.db.execute("INSERT INTO qq_model_events VALUES (?,?,'generated',?,?) ON CONFLICT(key) DO UPDATE SET state='generated',result=excluded.result",(key,fingerprint,canonical(command),time.time()))
                return command
            if self.w.cancel_event.is_set():raise Fault('任务已暂停/取消')
            from .deepseek_test import require_window,OffPeakDeferred
            try:require_window(self.w.s.config()['provider_url'])
            except OffPeakDeferred as wait:return self._park(parsed,event,key,fingerprint,scope,wait,authenticated)
            readiness=self.check()
            if readiness['missing']:return {'status':'not_configured','missing':readiness['missing'],'send':False,'fixture':not authenticated}
            since=time.time()-86400
            count=self.w.s.db.execute('SELECT count(*) FROM qq_model_attempts WHERE created>=?',(since,)).fetchone()[0]
            if count>=c['daily_limit']:raise Fault('QQ草稿24小时尝试次数上限')
            peer_count=self.w.s.db.execute('SELECT count(*) FROM qq_model_attempts WHERE scope=? AND created>=?',(scope,since)).fetchone()[0]
            if peer_count>=c['per_peer_daily_limit']:raise Fault('本会话24小时尝试次数上限')
            self.w.s.gate('qq-draft:'+scope,c['per_peer_interval_s'])
            self.w.s.gate('qq-draft',c['interval_s'])
            self.w.s.db.execute('INSERT INTO qq_model_attempts(event_key,created,scope) VALUES (?,?,?)',(key,time.time(),scope))
            if row is None:
                self.w.s.db.execute("INSERT INTO qq_model_events VALUES (?,?,'running',NULL,?)",(key,fingerprint,time.time()))
            else:
                self.w.s.db.execute("UPDATE qq_model_events SET state='running' WHERE key=?",(key,))
            try:
                context=self._context(c,parsed,key,authenticated);persona=context['personality'];memories=context['memories']
                conversation={'audience':'owner_private' if parsed.owner_private else 'public','scope':scope,'sender':digest([parsed.bot,parsed.sender]),'provenance':'qq-authenticated' if authenticated else 'fixture','memory_domain':'owner-private' if parsed.owner_private else 'public-scoped'}
                if parsed.owner_private:conversation['night_sleep_nudge_allowed']=self.w.owner_expression.night_allowed()
                result=self.w.model.chat(text,key,persona,memories,conversation=conversation,history=context['messages'])
                reply_text=validate_reply(result['text'],public=not parsed.owner_private,conversation_style=True)
                from bigfish_persona import format_reply
                reply_text=format_reply(reply_text)
                if parsed.owner_private:reply_text=self.w.owner_expression.filter_sleep(reply_text)
                expression=self.w.owner_expression.plan('qq',key,text,reply_text,owner_private=parsed.owner_private)
                # Prepared plain text segments only; never interpret CQ text.
                target={'user_id':parsed.peer} if parsed.kind=='friend' else {'group_id':parsed.peer}
                reply={'action':'send_private_msg' if parsed.kind=='friend' else 'send_group_msg','params':dict(target,message=[{'type':'text','data':{'text':reply_text}}]),'echo':key}
                value={'status':'model_draft','event_key':key,'reply_candidate':reply,'expression':expression,'model':result,'send':False,'fixture':not authenticated,'platform_authenticated':False,'transport_authenticated':authenticated,'context_memory_count':len(memories),'context_personality_version':persona['version'],'audience':conversation['audience'],'social_mode':c['social_mode'],'history_turns_used':len(context['messages']),'omitted_history':context['omitted_history']}
                if authenticated:self.w.growth.record_turn(self._growth_scope(parsed),key,'user',text,'qq-authenticated:传输验签接收；助手草稿尚未发送')
                else:self.w.growth.record_exchange(self._growth_scope(parsed),key,text,reply_text,'fixture:离线模拟草稿，未真实送达/不自动当主人事实')
                self.w.s.db.execute("UPDATE qq_model_events SET state='generated',result=? WHERE key=?",(canonical(value),key))
                self.w.s.db.execute('DELETE FROM qq_deferred_inputs WHERE key=?',(key,))
                return value
            except OffPeakDeferred as wait:
                return self._park(parsed,event,key,fingerprint,scope,wait,authenticated)
            except Exception:
                self.w.s.db.execute('DELETE FROM qq_deferred_inputs WHERE key=?',(key,))
                consumed=self.w.s.db.execute('SELECT 1 FROM charges WHERE id=?',('model:'+key,)).fetchone()
                status='uncertain' if consumed else 'blocked'
                self.w.s.db.execute('UPDATE qq_model_events SET state=? WHERE key=?',(status,key))
                return {'status':status,'event_key':key,'send':False,'fixture':not authenticated,'retry_requires_explicit_request':status=='blocked','note':'未发送；有费用记录则禁止重放并核对账单，否则修复配置/预算后可明确重试，错误正文不回显'}

def main():
    parser=argparse.ArgumentParser(description='QQ fixture/model draft harness; never sends messages')
    parser.add_argument('--data-dir',required=True);parser.add_argument('--fixture');parser.add_argument('--qq-config');parser.add_argument('--retry-blocked',action='store_true');args=parser.parse_args()
    # Reuse the service's exclusive process lock. Do not run beside the web app.
    import os
    root=Path(args.data_dir);root.mkdir(parents=True,exist_ok=True);lock=root/'runtime.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise SystemExit('已有服务使用该目录；先停止，不能同时运行fixture工具')
    os.close(fd);w=None
    try:
        from .service import Workbench
        w=Workbench(root)
        if args.qq_config:
            raw=Path(args.qq_config).read_bytes()
            if len(raw)>10000:raise Fault('QQ配置过大')
            w.qq.configure(json.loads(raw))
        result=w.qq.check()
        if args.fixture:
            raw=Path(args.fixture).read_bytes()
            if len(raw)>30000:raise Fault('fixture过大')
            result=w.qq.process_fixture(json.loads(raw),retry_blocked=args.retry_blocked)
        print(canonical(result))
    except (Fault,ValueError,TypeError,OSError):raise SystemExit('自检/fixture失败；未发送，不回显凭据或异常正文') from None
    finally:
        if w:w.close()
        lock.unlink(missing_ok=True)
if __name__=='__main__':main()
