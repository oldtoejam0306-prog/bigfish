import base64
import json
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from .runtime import State,HTTP,Fault,canonical,digest
from .bili import Bili,BV
from .model import Model

class Workbench:
    def __init__(self,root,http=None):
        import threading
        self.cancel_event=threading.Event()
        self.s=State(root);self.http=http or HTTP();self.bili=Bili(self.s,self.http);self.model=Model(self.s,self.http);self.model.cancel_check=self.cancel_event.is_set;self.bili.cancel_check=self.cancel_event.is_set
        from .queue import BrowseQueue
        self.queue=BrowseQueue(self)
        from .growth import Growth
        from .interests import Interests
        from .creation import CreationStudio
        self.growth=Growth(root);self.interests=Interests(self.s);self.creation=CreationStudio(root)
        from .qq import QQGlue
        self.qq=QQGlue(self);
        from .activity import Activities
        self.activities=Activities(self);
        self._qq_transport=None;self._qq_transport_binding=None;self._qq_origins={}
        self.s.db.executescript("CREATE TABLE IF NOT EXISTS qq_native_inbox(id TEXT PRIMARY KEY,event TEXT,state TEXT,result TEXT,created REAL); CREATE TABLE IF NOT EXISTS qq_sent_context(id TEXT PRIMARY KEY,kind TEXT,peer TEXT,owner_private INTEGER,event_key TEXT); CREATE TABLE IF NOT EXISTS manual_video_reads(account TEXT NOT NULL,bvid TEXT NOT NULL,coverage TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(account,bvid,coverage));")
        self.s.db.execute("UPDATE qq_native_inbox SET state='interrupted' WHERE state='processing'")
        from .interaction import InteractionWorker
        self.interaction=InteractionWorker(self)
        from .local_chat import LocalChat
        from .knowledge import KnowledgeBase
        self.knowledge=KnowledgeBase(self.s)
        from .return_nudge import ReturnNudge
        self.return_nudge=ReturnNudge(self.s)
        self.local_chat=LocalChat(self)
        from .characters import Characters
        self.characters=Characters(self.s)
        from .relationship import Relationship
        self.relationship=Relationship(self.s,self.characters)
        from .emotion import SimulatedEmotion
        self.emotion=SimulatedEmotion(self.s)
        from .stickers import Stickers
        self.stickers=Stickers(self.s)
        from .owner_expression import OwnerExpression
        self.owner_expression=OwnerExpression(self)
        from .computer_actions import ComputerActions
        self.computer_actions=ComputerActions(self.s)
        from .system_activity import SystemActivity
        self.system_activity=SystemActivity(self)
        from .video_prepare import VideoPreparation
        self.video_preparation=VideoPreparation(self)
        from .subtitle_search import SubtitleSearch
        self.subtitle_search=SubtitleSearch(self.s)
    def close(self):
        self.cancel_event.set();self.system_activity.close();self.interaction.close();self.queue.close();self.s.close()
    def selftest(self):
        test_table=self.s.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='single_model_tests'").fetchone()
        attempted=bool(test_table and self.s.db.execute('SELECT 1 FROM single_model_tests LIMIT 1').fetchone())
        from .media_tools import find_media_tools
        ffmpeg,ffprobe=find_media_tools()
        return dict(self.s.status(),single_test_attempted=attempted,bili_login=self.bili.login_status(),text_plan=self.model.plan(),visual_plan=self.model.plan(True),qq=self.qq.check(),qq_config=self.qq.config(),ffmpeg_present=bool(ffmpeg),ffprobe_present=bool(ffprobe),platform_tested='Linux only; Windows must self-test',outbound_requires='账号一致+启用外发+草稿逐条审核+手动发送',video_upload='UPOS contract implemented; authenticated validation pending')
    def character_overview(self):
        """Local summary only; no private memories, model calls or platform reads."""
        import sqlite3
        from datetime import datetime,timezone
        path=self.growth.root/'character.db';personality=None
        if path.exists():
            db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                row=db.execute('SELECT version,data FROM personality ORDER BY version DESC LIMIT 1').fetchone()
                if row:
                    data=json.loads(row[1]);personality={'version':row[0],'identity':data.get('identity',''),'traits':data.get('traits',[]),'creative_interest':data.get('creative_interest','')}
            finally:db.close()
        topics=self.interests.snapshot()['interests']
        with self.growth._db() as db:
            pending=db.execute("SELECT count(*) FROM growth_proposals WHERE state='pending'").fetchone()[0]
            proposals=db.execute("SELECT created FROM growth_proposals WHERE state='pending' ORDER BY created DESC LIMIT 6").fetchall()
        events=[]
        def add(kind,label,created,reference=None):
            try:
                stamp=datetime.fromtimestamp(created,timezone.utc) if type(created) in (int,float) else datetime.fromisoformat(created)
                if stamp.tzinfo is None or stamp.utcoffset() is None:return
                item={'kind':kind,'label':label,'created':stamp.astimezone(timezone.utc).isoformat()}
                if reference is not None:item['reference']=reference
                events.append(item)
            except (ValueError,TypeError,OverflowError,OSError):return
        coverage={'metadata':'仅元数据','subtitle':'仅字幕','sparse_frames':'仅稀疏画面','subtitle_and_frames':'字幕与稀疏画面','unavailable':'未完成阅读'}
        account=self.s.config()['account_mid'] or 'anonymous'
        for row in self.s.db.execute('SELECT bvid,coverage,created FROM public_video_observations WHERE account=? ORDER BY created DESC LIMIT 6',(account,)):
            if row['coverage'] in coverage:add('bili','B站资料：'+coverage[row['coverage']],row['created'],row['bvid'])
        manual={'metadata':'手动查看视频信息','subtitle':'手动取得字幕','no_subtitle':'手动查找字幕：未提供'}
        for row in self.s.db.execute('SELECT bvid,coverage,created FROM manual_video_reads WHERE account=? ORDER BY created DESC LIMIT 6',(account,)):
            if row['coverage'] in manual:add('bili',manual[row['coverage']],row['created'],row['bvid'])
            elif row['coverage'].startswith(('subtitle:p','no_subtitle:p')):
                kind,_,part=row['coverage'].partition(':p')
                if part.isdigit() and 2<=int(part)<=1000:add('bili',manual[kind]+' · 第'+part+'P',row['created'],row['bvid'])
        states={'pending':'待处理','local_reply_ready':'本地草稿待处理','model_draft':'草稿待审核','not_configured':'等待配置','blocked':'处理受阻','uncertain':'结果待核对','interrupted':'中断待核对','needs_reverification':'来源待重新核验','ignored':'已忽略'}
        for row in self.s.db.execute('SELECT state,created FROM qq_native_inbox ORDER BY created DESC LIMIT 6'):
            add('qq','QQ 收件：'+states.get(row['state'],'状态待核对'),row['created'])
        for row in proposals:add('growth','成长提案待审',row['created'])
        events.sort(key=lambda x:x['created'],reverse=True)
        return {'personality':personality,'interests':[{'topic':x['topic'],'weight':x['decayed_weight']} for x in topics[:8]],'pending_proposals':pending,'recent_activity':events[:6],'scope':'local_owner_view','model_called':False,'platform_called':False}
    def _record_manual_video_read(self,bvid,coverage,part_index=1):
        if not isinstance(bvid,str) or not BV.fullmatch(bvid) or coverage not in ('metadata','subtitle','no_subtitle') or type(part_index) is not int or not 1<=part_index<=1000:raise Fault('本机阅读记录无效')
        if part_index>1:coverage+=':p'+str(part_index)
        account=self.s.config()['account_mid'] or 'anonymous'
        self.s.db.execute('INSERT INTO manual_video_reads(account,bvid,coverage,created) VALUES (?,?,?,?) ON CONFLICT(account,bvid,coverage) DO UPDATE SET created=excluded.created',(account,bvid,coverage,time.time()))
    def read_popular(self):
        # One explicit user request may recheck an unknown login after a transient failure.
        # Platform risk/limit responses remain blocked until the existing cooldown expires.
        status=self.bili.login_status()
        if status.get('credential_present') and status.get('state')=='unknown' and status.get('reason') not in ('HTTP 412','HTTP 429','HTTP 567'):
            self.bili.login_status(probe=True)
        return self.bili.feed()
    def read_video_info(self,bvid):
        result=self.bili.info(bvid);self._record_manual_video_read(bvid,'metadata');return result
    def read_video_subtitle(self,bvid,refresh=False,part_index=1):
        result=self.bili.subtitle(bvid,refresh=refresh,part_index=part_index)
        self.subtitle_search.load(bvid,part_index,result)
        self._record_manual_video_read(bvid,'subtitle' if result.get('text') else 'no_subtitle',part_index)
        return result
    def single_model_test(self, request_id, confirmation, expected, ephemeral_key):
        import re
        from .deepseek_test import official_endpoint,require_window
        if confirmation!='确认执行一次模型测试':raise Fault('请明确确认本次单次测试')
        if not isinstance(ephemeral_key,str) or not 16<=len(ephemeral_key)<=512 or any(ord(x)<33 or ord(x)>126 for x in ephemeral_key):raise Fault('请在本次测试框输入有效Key')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id):raise Fault('测试编号须为8至64位字母、数字、下划线或连字符')
        c=self.s.config()
        if c['provider_url'] not in ('https://api.deepseek.com','https://api.deepseek.com/'):raise Fault('本次测试仅允许DeepSeek官方根地址')
        if c['model']!='deepseek-flash' or c['currency']!='CNY' or c['daily_budget_micro']!=1000000 or c['request_cap_micro']!=100000:
            raise Fault('本次仅允许Flash、人民币日上限1元及单次上限0.1元')
        if c['max_input_tokens']>2048 or c['max_output_tokens']>128 or c['input_price_micro_per_million']<2000000 or c['output_price_micro_per_million']<8000000:
            raise Fault('本次测试的token上限或保守价格不符合确认范围')
        if not isinstance(expected,dict) or set(expected)!={'model','daily_budget_micro','request_cap_micro'} or any(type(expected[k]) is not int for k in ('daily_budget_micro','request_cap_micro')):raise Fault('请核对模型与预算')
        if any(expected[k]!=c[k] for k in expected):raise Fault('模型或预算已变化，请重新核对')
        require_window(c['provider_url'])
        plan=self.model.plan(ephemeral_key_present=True)
        if not plan['ready']:raise Fault('模型测试尚未就绪：'+'；'.join(plan['missing']))
        with self.s.lock:
            self.s.db.execute('CREATE TABLE IF NOT EXISTS single_model_tests(id TEXT PRIMARY KEY,state TEXT NOT NULL)')
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                if self.s.db.execute('SELECT 1 FROM single_model_tests LIMIT 1').fetchone():raise Fault('此测试目录已执行或尝试过单次测试，不可重试')
                self.s.db.execute('INSERT INTO single_model_tests VALUES (?,?)',(request_id,'started'))
                self.s.db.commit()
            except Exception:
                self.s.db.rollback()
                raise
        try:
            test_http=self.http
            if type(test_http) is HTTP:
                import urllib.request
                from .runtime import NoRedirect
                test_http=HTTP();test_http.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            test_model=Model(self.s,test_http);test_model.cancel_check=self.cancel_event.is_set
            result=test_model.chat('你好，请用一句话介绍你自己。','single-test:'+request_id,{},[],conversation={'audience':'owner_private'},_ephemeral_key=ephemeral_key)
            self.s.db.execute("UPDATE single_model_tests SET state='completed' WHERE id=?",(request_id,))
            return {'test_id':request_id,'result':result,'sent_to_qq_or_bili':False,'automatic_retry':False}
        except Exception:
            self.s.db.execute("UPDATE single_model_tests SET state='uncertain_or_stopped' WHERE id=?",(request_id,))
            raise
        finally:
            ephemeral_key=None
    def analyze(self,bvid,request_id,mode='subtitle',local_video=None,*,refresh_subtitles=False,part_index=1):
        """Legacy callers may not start paid analysis without a prepared plan."""
        return {'status':'needs_video_confirmation','model_called':False,'note':'请先准备视频理解计划，核对最多调用次数和预算后逐项确认。'}
    def _confirmed_analyze(self,bvid,request_id,mode='subtitle',local_video=None,*,refresh_subtitles=False,part_index=1,expected_subtitle_signature=None,expected_local_signature=None):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80:raise Fault('请求ID须为1至80字符')
        if mode not in ('subtitle','frames','remote_frames','auto'):raise Fault('模式无效')
        if type(part_index) is not int or not 1<=part_index<=1000:raise Fault('分P序号无效')
        if self.cancel_event.is_set():raise Fault('任务已取消，未开始读取')
        if self.qq.config()['acceptance_owner_only']:return {'status':'needs_model_configuration','model_called':False,'note':'当前仅主人私聊验收，尚未启用视频推理'}
        subtitle=self.bili.subtitle(bvid,refresh=refresh_subtitles) if part_index==1 else self.bili.subtitle(bvid,refresh=refresh_subtitles,part_index=part_index)
        if expected_subtitle_signature is not None and self.video_preparation._subtitle_signature(subtitle)!=expected_subtitle_signature:raise Fault('确认后字幕或分 P 已变化，未调用模型，请重新准备')
        if mode=='auto':mode='subtitle' if subtitle['text'] else 'remote_frames'
        if not subtitle['text'] and mode=='subtitle':return {'status':'needs_frames','material':subtitle,'model_called':False,'note':'没有字幕，不会虚构理解；可选择本地视频稀疏帧'}
        if self.cancel_event.is_set():raise Fault('任务已暂停/取消，未调用模型')
        material=canonical({'title':subtitle['title'],'bvid':bvid,'cid':subtitle.get('cid'),'part_index':subtitle.get('part_index',1),'parts_total':subtitle.get('parts_total',1),'untrusted_subtitle':subtitle['text']})
        if mode!='subtitle' and len(material.encode())+1000>self.s.config()['max_input_tokens']:return {'status':'needs_model_configuration','model_called':False,'note':'带画面材料超过输入上限；请先使用字幕分段模式，未下载媒体'}
        plan=self.model.plan(mode!='subtitle')
        if plan.get('deferred'):return {'status':'deferred_for_off_peak','plan':plan,'model_called':False}
        if not plan['ready']:return {'status':'needs_model_configuration','material':subtitle,'plan':plan,'model_called':False}
        from .media_tools import find_media_tools
        ffmpeg,ffprobe=find_media_tools()
        if mode!='subtitle' and (not ffmpeg or not ffprobe):return {'status':'needs_dependencies','missing':['ffmpeg/ffprobe'],'model_called':False}
        images=[];sampling=None
        if mode=='remote_frames':
            info=self.bili.info(bvid);pages=info.get('pages') or [];total=len(pages) if pages else 1
            if part_index>total:raise Fault('分P序号超出视频范围')
            cid=pages[part_index-1]['cid'] if pages else info['cid']
            if subtitle.get('cid')!=cid:raise Fault('视频分P已变化，请重新读取后再试')
            framekey='remote-frames-v3:'+digest([self.bili.identity_key(),bvid,cid])
            frame_result=self.s.cached(framekey)
            if frame_result is None:
                with tempfile.TemporaryDirectory() as tmp:
                    target=Path(tmp)/'download.mp4';target.write_bytes(self.bili.media(bvid,part_index=part_index,expected_cid=cid));frame_result=extract_frames(target,cancel_check=self.cancel_event.is_set,with_metadata=True)
                self.s.cache(framekey,frame_result,300)
            if isinstance(frame_result,dict):images=frame_result['images'];sampling=frame_result['sampling']
            else:images=frame_result;sampling={'method':'injected-fixture','full_video_watched':False}
        if mode=='frames':
            if not local_video:raise Fault('请明确提供本地视频路径，未下载或采集桌面')
            if expected_local_signature is not None:
                try:before=self.video_preparation._local_file_signature(local_video)
                except (OSError,Fault):raise Fault('本地视频不可访问，未调用模型，请重新准备') from None
                if before!=expected_local_signature:raise Fault('本地视频已变化，未调用模型，请重新准备')
            frame_result=extract_frames(local_video,cancel_check=self.cancel_event.is_set,with_metadata=True)
            if expected_local_signature is not None:
                try:after=self.video_preparation._local_file_signature(local_video)
                except (OSError,Fault):raise Fault('抽帧期间本地视频不可访问，未调用模型') from None
                if after!=expected_local_signature:raise Fault('抽帧期间本地视频已变化，未调用模型')
            if isinstance(frame_result,dict):images=frame_result['images'];sampling=frame_result['sampling']
            else:images=frame_result;sampling={'method':'injected-fixture','full_video_watched':False}
        if mode!='subtitle' and not images:return {'status':'needs_frames','model_called':False,'note':'没有取得有效画面，不会伪称完成画面理解或调用模型'}
        if self.cancel_event.is_set():raise Fault('任务已取消，未调用模型')
        if sampling:material=canonical({'untrusted_material':material,'visual_sampling':sampling})
        result=self.model.summarize(material,request_id,images)
        if sampling:
            result=dict(result,sampling=sampling,coverage='字幕与稀疏画面' if subtitle['text'] else '仅稀疏画面，无字幕/音轨理解')
        key='analysis:'+digest([bvid,result['model'],subtitle['text'],result['text']] if part_index==1 else [bvid,subtitle['cid'],part_index,result['model'],subtitle['text'],result['text']])
        self.s.db.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?)',(key,canonical({'bvid':bvid,'result':result,'source':'B站材料+模型推断，非用户事实','input_source':{k:subtitle.get(k) for k in ('bvid','cid','part_index','parts_total','content_revision')},'sampling':sampling}),time.time()))
        return {'material_id':key,'result':result,'model_called':result.get('model_called',not result.get('cached',False))}
    def browse(self,request_id):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=80:raise Fault('需要本轮请求ID')
        feed=self.bili.feed();items=feed.get('list',[])[:3];results=[]
        for item in items:
            bvid=item.get('bvid','')
            if not BV.fullmatch(bvid):continue
            if not self.s.config()['paid_enabled']:
                results.append({'bvid':bvid,'title':item.get('title'),'status':'仅推荐元数据；模型未授权','model_called':False});continue
            try:results.append({'bvid':bvid,'analysis':self.analyze(bvid,request_id+':'+bvid)})
            except Fault as e:
                results.append({'bvid':bvid,'error':str(e)});break
        return {'results':results,'automatic_outbound':False,'source':'热门列表前3条，不是个性化推荐流','note':'只运行这一轮，不后台循环'}
    def qq_transport(self):
        from .qq_transport import OneBotHTTP,TransportConfig
        c=self.s.config();q=self.qq.config()
        if not c['qq_endpoint']:raise Fault('尚未配置本机OneBot HTTP合同；未验证SnowLuma实际支持')
        v=self.s.vault.read()
        config=TransportConfig(endpoint=c['qq_endpoint'],bot_id=q['bot_id'],owner_id=q['owner_id'],access_token=v.get('qq_access_token',''),event_secret=v.get('qq_event_secret',''),enabled=c['qq_sender_enabled'],transport_validated=c['qq_transport_validated'],owner_test_only=c['qq_transport_owner_test_only'] or q['acceptance_owner_only'],daily_limit=q['daily_limit'],peer_daily_limit=q['per_peer_daily_limit'],interval_s=q['interval_s'],owner_media_enabled=self.owner_expression.settings()['qq_media_enabled'],media_validated=self.owner_expression.settings()['qq_media_validated'])
        binding=digest([c['qq_endpoint'],q['bot_id'],q['owner_id'],v.get('qq_access_token',''),v.get('qq_event_secret','')])
        if self._qq_transport_binding!=binding:
            def allowed(kind,peer):
                policy=self.qq.config()
                if str(peer) in policy['blocked_users'] or (kind=='group' and str(peer) in policy['blocked_groups']):return False
                if kind=='group':return policy['groups_enabled'] and policy['social_mode']!='owner_only' and (policy['social_mode']=='account_social' or str(peer) in policy['group_allowlist'])
                return peer==policy['owner_id'] or policy['social_mode']=='account_social' or (policy['social_mode']=='allowlist' and str(peer) in policy['friend_allowlist'])
            self._qq_transport=OneBotHTTP(self.s,config,permission=allowed);self._qq_transport_binding=binding;self._qq_origins={}
        else:config.validate();self._qq_transport.c=config
        return self._qq_transport
    def accept_qq_event(self,body,headers):
        transport=self.qq_transport();trusted=transport.verify_event(body,headers)
        # A receipt is durable, but model processing is an explicit separate action.
        clean=transport.event_dict(trusted)
        policy=self.qq.config()
        if clean['message_type']=='group' and policy['group_reply_mode']=='mentioned' and not any(x['type']=='at' and str(x['data'].get('qq'))==str(policy['bot_id']) for x in clean['message']):
            self.s.db.execute("INSERT OR IGNORE INTO qq_native_inbox VALUES (?,?,'ignored',NULL,?)",(trusted.key,'{}',time.time()))
            return {'accepted':True,'ignored':True,'model_called':False,'sent':False,'quick_reply':False}
        existing=self.s.db.execute('SELECT 1 FROM qq_native_inbox WHERE id=?',(trusted.key,)).fetchone()
        self.s.db.execute("INSERT OR IGNORE INTO qq_native_inbox VALUES (?,?,'pending',NULL,?)",(trusted.key,canonical(clean),time.time()))
        self._qq_origins[trusted.key]=trusted
        from .social import parse_fixture
        if not existing and self.s.config()['qq_transport_validated'] and self.qq.config()['enabled'] and clean['message_type']=='private' and clean['user_id']==self.qq.config()['owner_id']:
            parsed=parse_fixture(clean,self.qq.config())
            if parsed.owner_private and (parsed.text.startswith(('/记住 ','/纠正记忆 ','/忘记记忆 ')) or parsed.text in ('/暂停自动交流','/解除暂停')):
                result=self.qq.process_authenticated(transport,trusted)
                if result.get('event_key'):self._qq_origins[result['event_key']]=trusted
                self.s.db.execute('UPDATE qq_native_inbox SET state=?,result=? WHERE id=?',('local_reply_ready' if result.get('status')=='model_draft' else result.get('status','blocked'),canonical(result),trusted.key))
        return {'accepted':True,'model_called':False,'sent':False,'quick_reply':False}
    def retry_qq_inbox(self,ident):
        self.qq_transport()
        row=self.s.db.execute('SELECT * FROM qq_native_inbox WHERE id=?',(ident,)).fetchone()
        if not row or row['state'] not in ('not_configured','blocked','needs_reverification'):raise Fault('此收件状态不能重放')
        old=json.loads(row['result']) if row['result'] else {};key=old.get('event_key')
        if key and self.s.db.execute('SELECT 1 FROM charges WHERE id=?',('model:'+key,)).fetchone():raise Fault('已有费用记录，不重放消息')
        if ident not in self._qq_origins:raise Fault('原验签来源已失效，须用户发送新消息；不伪造认证')
        self.s.db.execute("UPDATE qq_native_inbox SET state='pending' WHERE id=?",(ident,));return {'queued':True,'sent':False}
    def process_qq_inbox(self,max_jobs=1):
        if type(max_jobs) is not int or not 1<=max_jobs<=3:raise Fault('每次最多处理3个已验签事件')
        transport=self.qq_transport();results=[]
        if not self.s.config()['qq_transport_validated']:return {'results':[],'status':'needs_transport_validation','model_called':False,'sent':False}
        for row in self.s.db.execute("SELECT * FROM qq_native_inbox WHERE state IN ('pending','local_reply_ready') ORDER BY created LIMIT ?",(max_jobs,)).fetchall():
            origin=self._qq_origins.get(row['id'])
            if origin is None:
                self.s.db.execute("UPDATE qq_native_inbox SET state='needs_reverification' WHERE id=?",(row['id'],));continue
            self.s.db.execute("UPDATE qq_native_inbox SET state='processing' WHERE id=?",(row['id'],))
            result=None
            try:
                result=json.loads(row['result']) if row['state']=='local_reply_ready' and row['result'] else self.qq.process_authenticated(transport,origin)
                if result.get('event_key'):self._qq_origins[result['event_key']]=origin
                if result.get('status')=='model_draft':
                    result['transport_draft']=self._prepare_qq_result(result,transport,origin)
                self.s.db.execute('UPDATE qq_native_inbox SET state=?,result=? WHERE id=?',(result.get('status','blocked'),canonical(result),row['id']));results.append(result)
            except Exception:
                blocked={'status':'blocked','sent':False,'note':'未自动重试，检查本机身份/模型/预算'}
                if isinstance(result,dict) and result.get('event_key'):blocked['event_key']=result['event_key']
                self.s.db.execute("UPDATE qq_native_inbox SET state='blocked',result=? WHERE id=?",(canonical(blocked),row['id']));results.append(blocked)
        return {'results':results,'sender_auto_used':False,'snowluma_compatibility_verified':False}
    def resume_qq_deferred(self,max_jobs=1,*,authenticated_only=False):
        result=self.qq.resume_deferred(max_jobs,authenticated_only=authenticated_only)
        for value in result.get('results',[]):
            if not value.get('transport_authenticated'):continue
            key=value.get('event_key')
            for row in self.s.db.execute("SELECT id,result FROM qq_native_inbox WHERE state='deferred'").fetchall():
                old=json.loads(row['result']) if row['result'] else {}
                if old.get('event_key')==key:self.s.db.execute('UPDATE qq_native_inbox SET state=?,result=? WHERE id=?',(value['status'],canonical(value),row['id']))
            if value.get('status')=='model_draft':value['needs_local_send_review']=True
        return result
    def _prepare_qq_result(self,result,transport,origin):
        event_key=result['event_key'];candidate=json.loads(canonical(result['reply_candidate']))
        kind='friend' if candidate['action']=='send_private_msg' else 'group';peer=candidate['params']['user_id' if kind=='friend' else 'group_id']
        owner_private=kind=='friend' and peer==self.qq.config()['owner_id']
        old=self.s.db.execute('SELECT id,state,params FROM qq_transport_outbox WHERE id=?',(event_key,)).fetchone()
        if old:
            types=[part['type'] for part in json.loads(old['params'])['message']]
            return {'id':old['id'],'state':old['state'],'network_sent':False,'replayed':True,'expression':{'image_prepared':'image' in types,'voice_prepared':'record' in types,'sent':False}}
        expression_status={'image_prepared':False,'voice_prepared':False,'sent':False}
        if owner_private:
            from bigfish_persona import format_reply
            text=self.owner_expression.filter_sleep(format_reply(''.join(part['data']['text'] for part in candidate['params']['message'] if part['type']=='text')))
            candidate['params']['message']=[{'type':'text','data':{'text':text}}]
            if transport.c.owner_media_enabled and transport.c.media_validated:
                renderer=None
                if self.owner_expression.settings()['native_voice_authorized']:
                    from .system_voice import synthesize
                    renderer=getattr(self,'owner_voice_renderer',None) or (lambda value:synthesize(value,True,cancel_check=self.cancel_event.is_set))
                candidate['params']['message'],expression_status=self.owner_expression.segments(event_key,text,voice_renderer=renderer)
        draft=transport.prepare(candidate,origin=origin,local_authorization=origin is None);draft['expression']=expression_status
        self.s.db.execute('INSERT OR REPLACE INTO qq_sent_context VALUES (?,?,?,?,?)',(draft['id'],kind,str(peer),int(owner_private),event_key))
        return draft
    def prepare_qq_reply(self,event_key,confirmation):
        if confirmation!='确认准备QQ回复':raise Fault('先审查内容并确认准备QQ回复')
        row=self.s.db.execute("SELECT result FROM qq_model_events WHERE key=? AND state='generated'",(event_key,)).fetchone()
        if not row:raise Fault('没有可准备的QQ模型草稿')
        result=json.loads(row['result'])
        if result.get('transport_authenticated') is not True:raise Fault('离线fixture草稿不能当成真实QQ回复')
        return self._prepare_qq_result(result,self.qq_transport(),self._qq_origins.get(event_key))
    def send_qq(self,ident,confirmation):
        transport=self.qq_transport()
        meta=self.s.db.execute('SELECT * FROM qq_sent_context WHERE id=?',(ident,)).fetchone()
        if meta and meta['owner_private']:
            from bigfish_persona import is_sleep_nudge
            prepared_text=''.join(x['data']['text'] for x in transport.inspect(ident)['params']['message'] if x['type']=='text')
            if is_sleep_nudge(prepared_text) and self.owner_expression.filter_sleep(prepared_text)!=prepared_text:raise Fault('本夜已催睡，旧草稿需要重新审阅；未发送')
        receipt=transport.send(ident,confirmation,cancel_check=self.cancel_event.is_set)
        if meta and receipt.get('api_acknowledged') is True:
            stored=self.s.db.execute('SELECT result FROM qq_model_events WHERE key=?',(meta['event_key'],)).fetchone()
            if stored and json.loads(stored['result']).get('local_owner_command'):return receipt
            from .growth import Scope
            scope=Scope.owner() if meta['owner_private'] else (Scope.friend(meta['peer']) if meta['kind']=='friend' else Scope.group(meta['peer']))
            draft=transport.inspect(ident);text=''.join(x['data']['text'] for x in draft['params']['message'] if x['type']=='text')
            if meta['owner_private']:self.owner_expression.commit(meta['event_key'],text)
            self.growth.record_turn(scope,meta['event_key'],'assistant',text,'qq-authenticated:OneBot已确认消息ID，不代表对方已读')
        return receipt
    def qq_inbox_status(self):
        return {'inbox':[dict(x) for x in self.s.db.execute('SELECT id,state,created FROM qq_native_inbox ORDER BY created DESC LIMIT 100')],'transport_configured':bool(self.s.config()['qq_endpoint']),'sender_enabled':self.s.config()['qq_sender_enabled'],'actual_snowluma_compatibility':'未验收'}
    def local_scope(self,kind,peer=''):
        from .growth import Scope
        if kind=='owner':return Scope.owner()
        if kind not in ('friend','group') or not isinstance(peer,str) or not peer.isdigit() or int(peer)<=0:raise Fault('可信设置会话范围无效')
        return Scope.friend(str(int(peer))) if kind=='friend' else Scope.group(str(int(peer)))
    def growth_action(self,data):
        scope=self.local_scope(data.get('scope','owner'),data.get('peer',''));control=self.growth.controls();action=data.get('action');source=data.get('source','主人本机明确操作')
        if action=='context':return self.growth.context(scope,public_persona=self.qq.config()['public_persona'],include_memory=True)
        if action=='list_memories':return control.list_memories(scope,data.get('before_id'))
        if action=='remember':return {'id':control.remember(scope,data['content'],source,data.get('confidence',0.5),public_approved=data.get('public_approved',False))}
        if action=='correct_memory':return control.correct_memory(scope,data['id'],data['content'],source,public_approved=data.get('public_approved',False))
        if action=='delete_memory':return control.delete_memory(scope,data['id'],source)
        if action=='correct_turn':return control.correct_turn(scope,data['id'],data['content'],source)
        if action=='delete_turn':return control.delete_turn(scope,data['id'],source)
        if action=='delete_exchange':return control.delete_exchange(scope,data['event_key'],source)
        if action=='propose_personality':return control.propose_growth(data['instruction'],data['personality'],source)
        if action=='list_personality':
            if not scope.owner_private:raise Fault('成长提案仅可在主人本机范围审阅')
            return control.pending_growth(data.get('before_id'))
        if action=='reject_personality':
            if not scope.owner_private or data.get('confirmation')!='放弃成长提案':raise Fault('需要主人明确确认放弃成长提案')
            return control.reject_growth(data['id'])
        if action=='apply_personality':
            if data.get('confirmation')!='应用成长':raise Fault('需要明确输入应用成长')
            return {'version':control.apply_growth(data['id'])}
        raise Fault('未知成长操作')
    def remember(self,material_id):
        row=self.s.db.execute('SELECT content FROM materials WHERE id=?',(material_id,)).fetchone()
        if not row:raise Fault('材料不存在')
        from boundary import Store
        memory=Store(self.s.root/'character.db')
        try:
            data=json.loads(row[0]);content=data.get('result',{}).get('text') or data.get('text')
            if not content:raise Fault('没有可保存文本')
            return {'memory_id':memory.add_memory('friend','local-owner','long',content,'主人明确保存的B站材料 '+material_id+'；含模型推断，不是主人偏好',0.5)}
        finally:memory.close()
    def draft(self,kind,payload):
        if kind not in ('comment','dm','video'):raise Fault('草稿类型无效')
        if not isinstance(payload,dict):raise Fault('草稿无效')
        if kind=='comment':
            if not BV.fullmatch(str(payload.get('bvid',''))):raise Fault('BV号无效')
            allowed={'bvid','text','root','parent'}
            for key in ('root','parent'):
                if key in payload and (not isinstance(payload[key],str) or not payload[key].isdigit()):raise Fault('评论ID须为数字字符串')
        elif kind=='dm':
            if not isinstance(payload.get('recipient'),str) or not payload['recipient'].isdigit():raise Fault('收件人MID无效')
            allowed={'recipient','text'}
        else:
            allowed={'text','file','title','tid','tags','copyright','source'}
            if not isinstance(payload.get('title'),str) or not 1<=len(payload['title'])<=80:raise Fault('视频标题无效')
            if type(payload.get('tid')) is not int or not 1<=payload['tid']<=1000:raise Fault('视频分区tid无效，请从B站创作中心确认')
            if not isinstance(payload.get('tags'),str) or not 1<=len(payload['tags'])<=200:raise Fault('需提供视频标签')
            if payload.get('copyright') not in (1,2) or type(payload.get('copyright')) is not int:raise Fault('版权类型须为1原创或2转载')
            if payload['copyright']==2 and (not isinstance(payload.get('source'),str) or not payload['source'].startswith('https://')):raise Fault('转载需明确来源链接')
        if set(payload)-allowed or not isinstance(payload.get('text'),str) or not 1<=len(payload['text'])<=1000:raise Fault('正文或字段无效')
        account=self.s.config()['account_mid']
        if not account:raise Fault('先配置独立账号MID，草稿绑定账号')
        if kind=='video':
            from .upload import inspect_video
            payload=dict(payload,**inspect_video(payload.get('file','')))
            for old in self.s.db.execute("SELECT payload FROM drafts WHERE kind='video' AND state NOT IN ('rejected')"):
                if json.loads(old[0]).get('file_sha256')==payload['file_sha256']:raise Fault('同一视频已有草稿或发送记录，先人工核对，不重复创建')
        ident=secrets.token_hex(16)
        self.s.db.execute("INSERT INTO drafts VALUES (?,?,?,?,'pending',?,NULL)",(ident,kind,canonical(payload),account,time.time()))
        return {'id':ident,'state':'pending','dry_run':True,'network_sent':False}
    def review(self,ident,decision):
        if decision not in ('approve','reject'):raise Fault('审核动作无效')
        row=self.s.db.execute('SELECT * FROM drafts WHERE id=?',(ident,)).fetchone()
        if not row or row['state']!='pending':raise Fault('草稿不是待审状态')
        if row['kind']=='video' and decision=='approve':
            from .upload import inspect_video
            payload=json.loads(row['payload'])
            if inspect_video(payload['file'])!={k:payload[k] for k in ('file','file_bytes','file_sha256')}:raise Fault('视频已改变，不能批准')
        self.s.db.execute("UPDATE drafts SET state=? WHERE id=? AND state='pending'",('approved' if decision=='approve' else 'rejected',ident))
        return {'id':ident,'state':'approved' if decision=='approve' else 'rejected'}
    def execute(self,ident,confirmation):
        if self.cancel_event.is_set():raise Fault('任务已取消，请先明确恢复')
        if confirmation!='确认发送':raise Fault('需逐次输入确认发送')
        c=self.s.config()
        if not c['outbound_enabled']:raise Fault('外发开关关闭')
        with self.s.lock:
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                row=self.s.db.execute('SELECT * FROM drafts WHERE id=?',(ident,)).fetchone()
                if not row or row['state']!='approved' or row['account']!=c['account_mid']:raise Fault('草稿未批准、已处理或账号不一致')
                count=self.s.db.execute('SELECT count(*) FROM write_attempts WHERE created>=?',(time.time()-86400,)).fetchone()[0]
                if count>=c['write_daily_limit']:raise Fault('24小时外发次数上限')
                self.s.db.execute('INSERT INTO write_attempts VALUES (?,?)',(ident,time.time()))
                self.s.db.execute("UPDATE drafts SET state='sending' WHERE id=?",(ident,));self.s.db.commit()
            except Exception:self.s.db.rollback();raise
        try:
            receipt=self.bili.send(row['kind'],json.loads(row['payload']),self.cancel_event.is_set,lambda phase:self.s.db.execute('INSERT OR REPLACE INTO controls VALUES (?,?)',('upload_last_phase_at:'+phase,time.time())))
            self.s.db.execute("UPDATE drafts SET state='sent',result=? WHERE id=?",(canonical(receipt),ident))
            return receipt
        except Exception:
            self.s.db.execute("UPDATE drafts SET state='uncertain' WHERE id=?",(ident,))
            raise Fault('未确认发送结果，禁止自动重发，请到独立账号核对') from None
    def draft_detail(self,ident):
        row=self.s.db.execute('SELECT * FROM drafts WHERE id=?',(ident,)).fetchone()
        if not row:raise Fault('草稿不存在')
        result=dict(row);result['payload']=json.loads(result['payload']);return result

def extract_frames(filename,cancel_check=None,*,with_metadata=False):
    def check_cancel():
        if cancel_check and cancel_check():raise Fault('抽帧已取消；不会继续处理剩余画面')
    check_cancel()
    file=Path(filename).expanduser().resolve()
    if not file.is_file() or file.suffix.lower() not in ('.mp4','.mkv','.webm','.mov') or file.stat().st_size>2_000_000_000:raise Fault('视频文件不存在、类型不支持或超过2GB')
    # Reject playlists disguised as video before a demuxer can follow references.
    with file.open('rb') as stream:header=stream.read(12)
    if not (header[4:8]==b'ftyp' or header[:4]==bytes.fromhex('1a45dfa3')):raise Fault('仅支持带标准容器标识的MP4/MOV或Matroska/WebM；不接受播放列表')
    from .media_tools import find_media_tools
    ffmpeg,ffprobe=find_media_tools()
    if not ffmpeg or not ffprobe:raise Fault('缺少ffmpeg/ffprobe，请使用可信发行版安装后再自检')
    try:
        probe=subprocess.run([ffprobe,'-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm','-show_entries','format=duration','-of','json',str(file)],capture_output=True,timeout=20,check=True)
        check_cancel()
        duration=float(json.loads(probe.stdout)['format']['duration'])
        if not 0<duration<86400:raise Fault('视频时长无效')
        from .frame_selection import select_indices
        candidates=[duration*fraction for fraction in (0.08,0.24,0.40,0.56,0.72,0.88)];rgb=[]
        for position in candidates:
            check_cancel()
            sample=subprocess.run([ffmpeg,'-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm','-ss',str(position),'-i',str(file),'-frames:v','1','-vf','scale=64:36','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,timeout=30,check=True)
            if len(sample.stdout)!=64*36*3:raise Fault('候选RGB帧大小无效')
            rgb.append(sample.stdout)
        selection=select_indices(rgb,64,36,limit=3);check_cancel();images=[]
        with tempfile.TemporaryDirectory() as tmp:
            for index in selection['indices']:
                check_cancel();out=Path(tmp)/f'{index}.jpg'
                subprocess.run([ffmpeg,'-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm','-ss',str(candidates[index]),'-i',str(file),'-frames:v','1','-vf','scale=480:-2','-q:v','5',str(out)],capture_output=True,timeout=30,check=True)
                check_cancel();data=out.read_bytes()
                if len(data)>250000:raise Fault('关键帧过大')
                images.append('data:image/jpeg;base64,'+base64.b64encode(data).decode())
        metadata=dict(selection,timestamps=[round(candidates[i],3) for i in selection['indices']],candidates=len(candidates),full_video_watched=False,audio_understood=False)
        return {'images':images,'sampling':metadata} if with_metadata else images
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):raise Fault('本地抽帧失败或视频格式不支持') from None
