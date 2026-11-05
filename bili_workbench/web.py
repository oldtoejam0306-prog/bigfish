import argparse
import json
import os
import secrets
import threading
import webbrowser
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from .runtime import Fault,canonical
from .service import Workbench

class Server(ThreadingHTTPServer):
    def __init__(self,address,workbench,preview=False,duplex_transport=None):
        self.workbench=workbench;self.preview=preview;self.duplex_transport=duplex_transport;self.duplex_lease=None;self.duplex_lock=threading.Lock();self.duplex_activation_timer=None;self.duplex_activation_timeout=120;self.supervised=False;self.restart_requested=False;self.backend_signature=None;self.token=secrets.token_urlsafe(32);super().__init__(address,Handler)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,value,html=False):
        raw=value.encode() if html else canonical(value).encode()
        self.send_response(code);self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:");self.end_headers();self.wfile.write(raw)
    def reject_post(self,code,value):
        # Read and discard only a bounded, unambiguous body before closing.
        # Closing TCP with unread request bytes can reset the connection before
        # the client receives the rejection. Never parse or execute this body.
        import time
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)==1 and self.headers.get('Transfer-Encoding') is None:
            previous=self.connection.gettimeout()
            try:
                remaining=int(lengths[0])
                if 0<remaining<=1_000_000:
                    deadline=time.monotonic()+.25
                    while remaining:
                        available=deadline-time.monotonic()
                        if available<=0:break
                        self.connection.settimeout(available)
                        chunk=self.rfile.read1(min(remaining,65536))
                        if not chunk:break
                        remaining-=len(chunk)
            except (ValueError,OSError,TimeoutError):pass
            finally:self.connection.settimeout(previous)
        self.close_connection=True
        return self.reply(code,value)

    def authorized(self):
        host='127.0.0.1:'+str(self.server.server_port)
        origin=self.headers.get('Origin')
        return self.headers.get('Host')==host and (not origin or origin=='http://'+host) and secrets.compare_digest(self.headers.get('X-Local-Token',''),self.server.token)
    def do_GET(self):
        if self.path=='/':
            page=(Path(__file__).parent/'index.html').read_text(encoding='utf-8')
            if self.server.preview:page=page.replace('<html lang="zh">','<html lang="zh" data-preview="true">',1)
            return self.reply(200,page,True)
        if self.path=='/single-test':return self.reply(200,(Path(__file__).parent/'single_test.html').read_text(encoding='utf-8'),True)
        if self.path in ('/duplex-client.js','/duplex-audio.js','/report-view.js','/sticker-chat.js','/local-recovery.js'):
            asset={'/duplex-client.js':'duplex_client.js','/duplex-audio.js':'duplex_audio.js','/report-view.js':'report_view.js','/sticker-chat.js':'sticker_chat.js','/local-recovery.js':'local_recovery.js'}[self.path]
            raw=(Path(__file__).parent/asset).read_bytes()
            self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
        if not self.authorized():return self.reply(403,{'error':'本机页面会话无效'})
        if self.path=='/api/ui-version':
            import hashlib
            page=(Path(__file__).parent/'index.html').read_bytes()
            script=(Path(__file__).parent/'duplex_client.js').read_bytes()
            audio=(Path(__file__).parent/'duplex_audio.js').read_bytes()
            reports=(Path(__file__).parent/'report_view.js').read_bytes()
            stickers=(Path(__file__).parent/'sticker_chat.js').read_bytes()
            recovery=(Path(__file__).parent/'local_recovery.js').read_bytes()
            from .hot_reload import backend_version
            changed=bool(self.server.backend_signature and backend_version()!=self.server.backend_signature)
            return self.reply(200,{'version':hashlib.sha256(page+b'\0'+script+b'\0'+audio+b'\0'+reports+b'\0'+stickers+b'\0'+recovery).hexdigest(),'supervised':self.server.supervised,'backend_changed':changed})
        if self.path=='/api/status':
            with self.server.workbench.s.lock:return self.reply(200,self.server.workbench.selftest())
        if self.path=='/api/local-chat/history':
            with self.server.workbench.s.lock:return self.reply(200,self.server.workbench.local_chat.history())
        if self.path.startswith('/api/local-chat/recovery/'):
            import re,sqlite3
            if self.path=='/api/local-chat/recovery/current':
                try:return self.reply(200,self.server.workbench.local_chat.recovery.current())
                except (Fault,sqlite3.Error):return self.reply(503,{'error':'暂时无法确认当前角色，未重放操作'})
            match=re.fullmatch(r'/api/local-chat/recovery/(default|[0-9a-f]{24})/(pending|(?:chat|memory)/[A-Za-z0-9_-]{8,64})',self.path)
            if not match:return self.reply(400,{'error':'核对地址无效'})
            try:
                recovery=self.server.workbench.local_chat.recovery
                result=recovery.pending(match[1]) if match[2]=='pending' else recovery.status(match[1],*match[2].split('/'))
                return self.reply(200,result)
            except Fault as error:return self.reply(400,{'error':str(error)})
            except sqlite3.Error:return self.reply(503,{'error':'本机记录暂不可读，请稍后再次检查结果；未重放操作'})
        if self.path.startswith('/api/local-chat/sticker-image/'):
            import re
            match=re.fullmatch(r'/api/local-chat/sticker-image/(default|[0-9a-f]{24})/([1-9][0-9]{0,15})',self.path)
            if not match:return self.reply(400,{'error':'表情对话地址无效'})
            try:return self.reply(200,self.server.workbench.local_chat.sticker_image(int(match[2]),match[1]))
            except Fault as error:return self.reply(400,{'error':str(error)})
            except Exception:return self.reply(503,{'error':'暂时无法读取本机表情，请重新读取对话核对'})
        if self.path=='/api/knowledge/list':
            return self.reply(200,self.server.workbench.knowledge.list())
        if self.path=='/api/return-nudge':
            return self.reply(200,self.server.workbench.return_nudge.status())
        if self.path=='/api/local-relationship':
            return self.reply(200,self.server.workbench.relationship.snapshot())
        if self.path=='/api/characters':
            return self.reply(200,self.server.workbench.characters.list())
        if self.path=='/api/emotion':
            w=self.server.workbench
            return self.reply(200,w.emotion.status(w.characters.current()['id']))
        if self.path=='/api/stickers':
            return self.reply(200,self.server.workbench.stickers.list())
        if self.path=='/api/owner-expression':return self.reply(200,self.server.workbench.owner_expression.settings())
        if self.path=='/api/voice/readiness':
            if self.server.preview:return self.reply(200,{'ready':False,'checked':False,'reason':'只读预览不探测系统语音，也不开麦或执行语音脚本'})
            from .voice_input import readiness
            return self.reply(200,readiness())
        if self.path=='/api/computer-actions':
            return self.reply(200,self.server.workbench.computer_actions.audit())
        if self.path=='/api/system-activity/status':
            return self.reply(200,self.server.workbench.system_activity.status())
        if self.path in ('/api/system-activity/day','/api/system-activity/week'):
            return self.reply(200,self.server.workbench.system_activity.report(self.path.rsplit('/',1)[-1]))
        if self.path in ('/api/activity-report/day','/api/activity-report/week'):
            from .local_report import activity_report
            return self.reply(200,activity_report(self.server.workbench,self.path.rsplit('/',1)[-1]))
        if self.path=='/api/character-overview':
            with self.server.workbench.s.lock:return self.reply(200,self.server.workbench.character_overview())
        self.reply(404,{'error':'未知接口'})
    def do_POST(self):
        if self.server.preview:return self.reject_post(403,{'error':'只读预览不执行写入、平台操作或模型请求'})
        if self.path=='/onebot/event':
            try:
                host='127.0.0.1:'+str(self.server.server_port)
                if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!=host:raise Fault('只允许本机事件')
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=65536:raise Fault('事件大小无效')
                pairs=list(self.headers.items());names=[name.lower() for name,_ in pairs]
                if len(names)!=len(set(names)):raise Fault('事件请求头存在重复字段')
                self.server.workbench.accept_qq_event(self.rfile.read(n),dict(pairs))
                self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
            except Exception:
                self.send_response(403);self.send_header('Content-Length','0');self.end_headers()
            return
        if not self.authorized():return self.reject_post(403,{'error':'本机页面会话无效'})
        if self.path in ('/api/duplex/activate','/api/duplex/deactivate','/api/duplex/recover','/api/duplex/start','/api/duplex/feed','/api/duplex/poll','/api/duplex/stop'):return self.duplex_post()
        if self.path=='/api/stop':return self.stop_post()
        if self.path in ('/api/queue/pause','/api/qq/worker-pause','/api/cancel'):self.server.workbench.cancel_event.set()
        with self.server.workbench.s.lock:return self.post_locked()
    def stop_post(self):
        # A voice worker may need the DB lock to finish accounting. Never hold
        # that lock while waiting for it, or acquire DB then duplex locks.
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=1024 or self.headers.get('Transfer-Encoding'):raise Fault('停止请求格式无效')
            if json.loads(self.rfile.read(n))!={}:raise Fault('停止请求字段无效')
            self.server.workbench.cancel_event.set()
            with self.server.duplex_lock:
                transport=self.server.duplex_transport
                if transport is not None:
                    if not transport.close():raise Fault('语音组件仍在停止，请先检查后再关闭工作台')
                    self.server.duplex_transport=None;self.server.duplex_lease=None
                    if self.server.duplex_activation_timer:self.server.duplex_activation_timer.cancel();self.server.duplex_activation_timer=None
            self.reply(200,{'stopping':True});threading.Thread(target=self.server.shutdown,daemon=True).start()
        except Fault as e:self.reply(400,{'error':str(e)})
        except (ValueError,TypeError):self.reply(400,{'error':'停止请求格式无效'})
        except Exception:self.reply(500,{'error':'停止结果不明，请检查工作台状态'})
    def duplex_post(self):
        # Duplex transport has its own synchronization: a running model must
        # not block audio cancellation or playback polling on the DB lock.
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=8192 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise Fault('语音消息大小或格式无效')
            payload=json.loads(self.rfile.read(n));operation=self.path.rsplit('/',1)[-1]
            if not isinstance(payload,dict):raise Fault('语音消息无效')
            if operation=='activate':
                allowed={'speech_confirmation','model_confirmation','echo_mode','include_memory','include_knowledge','context_confirmation'}
                if set(payload)-allowed:raise Fault('语音启动字段无效')
                with self.server.duplex_lock:
                    if self.server.duplex_transport is not None:return self.reply(409,{'error':'已有语音会话或上次停止尚未确认','code':'voice_session_exists'})
                    from .duplex_factory import build_local_duplex_transport
                    self.server.duplex_transport=build_local_duplex_transport(self.server.workbench,**payload)
                    self.server.duplex_lease=secrets.token_hex(16)
                    lease=self.server.duplex_lease;transport=self.server.duplex_transport
                    def expire_unstarted():
                        with self.server.duplex_lock:
                            if self.server.duplex_lease!=lease or self.server.duplex_transport is not transport:return
                            bridge=getattr(getattr(transport,'wire',None),'bridge',None)
                            if bridge is None or bridge.id is not None:return
                            if transport.close():self.server.duplex_transport=None;self.server.duplex_lease=None
                    timer=threading.Timer(self.server.duplex_activation_timeout,expire_unstarted);timer.daemon=True;self.server.duplex_activation_timer=timer;timer.start()
                return self.reply(200,{'activated':True,'lease':lease,'microphone_started':False,'model_called':False})
            if operation=='recover':
                if payload!={'confirmation':'确认关闭遗留语音会话'}:raise Fault('需要明确确认关闭遗留语音会话')
                with self.server.duplex_lock:
                    transport=self.server.duplex_transport
                    if transport is None:return self.reply(200,{'state':'stopped'})
                    if not transport.close():return self.reply(200,{'state':'stopping','note':'语音组件仍在停止，请稍后检查'})
                    self.server.duplex_transport=None;self.server.duplex_lease=None
                    if self.server.duplex_activation_timer:self.server.duplex_activation_timer.cancel();self.server.duplex_activation_timer=None
                return self.reply(200,{'state':'stopped'})
            if operation=='deactivate':
                with self.server.duplex_lock:
                    transport=self.server.duplex_transport
                    if transport is None:return self.reply(200,{'state':'stopped'})
                    self.check_duplex_lease(payload)
                    if not transport.close():return self.reply(200,{'state':'stopping','note':'语音组件仍在停止，请稍后检查'})
                    self.server.duplex_transport=None;self.server.duplex_lease=None
                    if self.server.duplex_activation_timer:self.server.duplex_activation_timer.cancel();self.server.duplex_activation_timer=None
                return self.reply(200,{'state':'stopped'})
            if operation=='start':
                with self.server.duplex_lock:
                    transport=self.server.duplex_transport
                    if transport is None:return self.reply(503,{'error':'实时语音传输尚未配置'})
                    self.check_duplex_lease(payload)
                    payload=dict(payload);payload.pop('lease',None)
                    result=transport.dispatch(operation,payload)
                    if self.server.duplex_activation_timer:self.server.duplex_activation_timer.cancel();self.server.duplex_activation_timer=None
                return self.reply(200,result)
            transport=self.server.duplex_transport
            if transport is None:return self.reply(503,{'error':'实时语音传输尚未配置'})
            result=transport.dispatch(operation,payload)
            return self.reply(200,result)
        except Fault as error:return self.reply(400,{'error':str(error)})
        except (ValueError,KeyError,TypeError):return self.reply(400,{'error':'语音消息无效'})
        except Exception:return self.reply(503,{'error':'语音消息结果不明，请停止本地传输并检查会话状态'})
    def check_duplex_lease(self,payload):
        lease=self.server.duplex_lease
        if lease is None:
            if 'lease' in payload:raise Fault('语音租约无效')
            return
        value=payload.get('lease')
        if not isinstance(value,str) or not value.isascii() or len(value)!=32 or not secrets.compare_digest(value,lease):raise Fault('语音租约无效')
        if self.path.endswith('/deactivate') and set(payload)!={'lease'}:raise Fault('语音关闭字段无效')

    def post_locked(self):
        if not self.authorized():return self.reject_post(403,{'error':'本机页面会话无效'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=1_000_000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise Fault('请求体无效')
            d=json.loads(self.rfile.read(n));w=self.server.workbench
            if not isinstance(d,dict):raise Fault('参数无效')
            if self.path=='/api/reload-service':
                if self.server.duplex_transport is not None:raise Fault('请先停止并关闭语音会话')
                if not self.server.supervised:raise Fault('请从支持重载的启动器打开工作台')
                from .hot_reload import blocking_reason
                reason=blocking_reason(w)
                if reason:raise Fault(reason+'，请先处理后再重载')
                self.server.restart_requested=True
                self.reply(200,{'restarting':True,'automatic_model_call':False})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            elif self.path=='/api/config':
                c=d.get('config',{});v=w.s.vault.read();updates={}
                for field in ('api_key','qq_access_token','qq_event_secret'):
                    value=d.get(field,'')
                    if not isinstance(value,str) or len(value)>16000 or any(ord(x)<32 for x in value):raise Fault('凭据格式无效')
                    if value:updates[field]=value
                w.s.configure(c)
                if updates:v.update(updates);w.s.vault.save(v)
                w.interaction.start_if_enabled();result={'saved':True}
            elif self.path=='/api/deepseek-test/prepare':
                from .deepseek_test import prepare
                result=prepare(w)
            elif self.path=='/api/deepseek-test/status':
                from .deepseek_test import window
                result=window()
            elif self.path=='/api/preset':
                from .presets import apply_preset
                result=apply_preset(w.s,d['name'],d.get('workspace_id'))
            elif self.path=='/api/qq/config':result=w.qq.configure(d)
            elif self.path=='/api/qq/resume-deferred':result=w.resume_qq_deferred(d.get('max_jobs',1))
            elif self.path=='/api/qq/setup-check':result=w.qq.check()
            elif self.path=='/api/qq/worker-status':result=w.interaction.state()
            elif self.path=='/api/qq/worker-start':result=w.interaction.start_if_enabled()
            elif self.path=='/api/qq/worker-pause':result=w.interaction.pause()
            elif self.path=='/api/qq/inbox':result=w.qq_inbox_status()
            elif self.path=='/api/qq/retry-inbox':result=w.retry_qq_inbox(d['id'])
            elif self.path=='/api/qq/process-inbox':result=w.process_qq_inbox(d.get('max_jobs',1))
            elif self.path=='/api/qq/prepare-send':result=w.prepare_qq_reply(d['event_key'],d.get('confirmation'))
            elif self.path=='/api/qq/transport-status':result=w.qq_transport().status()
            elif self.path=='/api/qq/send-detail':result=w.qq_transport().inspect(d['id'])
            elif self.path=='/api/qq/send':result=w.send_qq(d['id'],d.get('confirmation'))
            elif self.path=='/api/local-chat/send':result=w.local_chat.send(d.get('request_id'),d.get('text'),d.get('include_memory',False),d.get('include_knowledge',False),expected_character=d.get('character_id'))
            elif self.path=='/api/local-chat/recovery/acknowledge':result=w.local_chat.recovery.acknowledge(d.get('character_id'),d.get('kind'),d.get('request_id'),d.get('confirmation'))
            elif self.path=='/api/local-chat/sticker':result=w.local_chat.send_sticker(d.get('request_id'),d.get('sticker_id'),d.get('character_id'))
            elif self.path=='/api/local-chat/remember-turn':result=w.local_chat.remember_turn(d.get('request_id'),d.get('turn_id'),d.get('content'),d.get('confirmation'))
            elif self.path=='/api/knowledge/add':result=w.knowledge.add(d.get('title'),d.get('body'),d.get('source',''))
            elif self.path=='/api/knowledge/get':result=w.knowledge.get(d.get('id'))
            elif self.path=='/api/knowledge/delete':result=w.knowledge.delete(d.get('id'))
            elif self.path=='/api/return-nudge/configure':result=w.return_nudge.configure(d.get('enabled'),d.get('minimum_seconds'),d.get('cooldown_seconds'),d.get('message'))
            elif self.path=='/api/return-nudge/returned':result=w.return_nudge.returned(d.get('away_seconds'))
            elif self.path=='/api/local-relationship/tone':result=w.relationship.set_tone(d.get('tone'))
            elif self.path=='/api/characters/create':result=w.characters.create(d.get('name'),d.get('identity'),d.get('traits'))
            elif self.path=='/api/characters/select':result=w.characters.select(d.get('id'))
            elif self.path=='/api/characters/delete':result=w.characters.delete(d.get('id'))
            elif self.path=='/api/characters/restore':result=w.characters.restore(d.get('id'))
            elif self.path=='/api/emotion/configure':result=w.emotion.configure(w.characters.current()['id'],d.get('enabled'))
            elif self.path=='/api/stickers/add':result=w.stickers.add(d.get('name'),d.get('png_base64'))
            elif self.path=='/api/owner-expression':
                values=d.get('preferences')
                if isinstance(values,dict) and any(values.get(k) is True for k in ('native_voice_authorized','qq_media_validated')) and d.get('confirmation')!='已完成本机语音与主人媒体验收':raise Fault('真实语音执行与QQ媒体需要先完成独立验收')
                result=w.owner_expression.configure(values)
            elif self.path=='/api/stickers/get':result=w.stickers.get(d.get('id'))
            elif self.path=='/api/stickers/delete':result=w.stickers.delete(d.get('id'))
            elif self.path=='/api/voice/transcribe':
                from .voice_input import transcribe_wav
                result=transcribe_wav(d.get('wav_base64'))
            elif self.path=='/api/screen/analyze':
                from .screen_read import analyze_screenshot
                result=analyze_screenshot(w,d.get('request_id'),d.get('jpeg_base64'),d.get('question'),d.get('confirmation'))
            elif self.path=='/api/computer-actions/prepare':result=w.computer_actions.prepare(d.get('action'))
            elif self.path=='/api/computer-actions/execute':result=w.computer_actions.execute(d.get('id'),d.get('confirmation'))
            elif self.path=='/api/system-activity/start':result=w.system_activity.start(d.get('activity'),d.get('reminder'),d.get('model_reply'),d.get('away_seconds'),d.get('cooldown_seconds'),d.get('daily_limit'),d.get('activity_confirmation'),d.get('model_confirmation'),include_memory=d.get('include_memory',False),include_history=d.get('include_history',False),context_confirmation=d.get('context_confirmation',''))
            elif self.path=='/api/system-activity/stop':result=w.system_activity.stop()
            elif self.path=='/api/system-activity/reply-draft':result=w.system_activity.reply_draft(d.get('event_id'))
            elif self.path=='/api/system-activity/clear':result=w.system_activity.clear_report(d.get('confirmation'))
            elif self.path=='/api/video-analysis/prepare':result=w.video_preparation.prepare(d.get('reference'),d.get('mode'),d.get('part_index'),d.get('local_video',''))
            elif self.path=='/api/video-analysis/execute':result=w.video_preparation.execute(d.get('id'),d.get('confirmation'))
            elif self.path=='/api/video-reference':
                from .bili import parse_video_reference
                result=parse_video_reference(d.get('reference'))
            elif self.path=='/api/growth':result=w.growth_action(d)
            elif self.path=='/api/interests':result=w.interests.snapshot()
            elif self.path=='/api/interests/correct':result=w.interests.correct(d['topic'],d['weight'],d['reason'])
            elif self.path=='/api/interests/delete':result=w.interests.delete(d['topic'],d['reason'])
            elif self.path=='/api/comment-reply':result=w.activities.comment_reply(d['bvid'],d['comment_id'],d['request_id'],d.get('page',1))
            elif self.path=='/api/creation/script':result=w.activities.creation_script(d['topic'],d['request_id'])
            elif self.path=='/api/activities/retry':result=w.activities.retry(d['id'])
            elif self.path=='/api/activities/resume':result=w.activities.resume(d.get('max_jobs',1))
            elif self.path=='/api/creation/plan':
                result=w.creation.plan(d['script'])
                w.interests.feedback(d['script']['title'][:40],0.1,source='character_public_feedback',evidence='主人已批准的公开创作分镜:'+result['id'])
            elif self.path=='/api/voice/preview':
                from .system_voice import synthesize
                raw=synthesize(d.get('text'),d.get('confirmation'))
                self.send_response(200);self.send_header('Content-Type','audio/wav');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw);return
            elif self.path=='/api/creation/media':
                checked=w.creation.inspect(d['id'])
                if checked['file_bytes']>50000000:raise Fault('本机预览限50MB，请使用本地播放器')
                raw=Path(checked['file']).read_bytes();self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
            elif self.path=='/api/creation/captions':
                import html
                plan=w.creation.preview(d['id']);lines=['WEBVTT',''];elapsed=0
                def stamp(seconds):return str(seconds//3600).zfill(2)+':'+str(seconds//60%60).zfill(2)+':'+str(seconds%60).zfill(2)+'.000'
                for i,scene in enumerate(plan['scenes']):
                    text=('【AI·大肥鱼】' if i==0 else '')+scene['text'];lines += [stamp(elapsed)+' --> '+stamp(elapsed+scene['duration']),html.escape(text),''];elapsed+=scene['duration']
                raw='\n'.join(lines).encode();self.send_response(200);self.send_header('Content-Type','text/vtt; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
            elif self.path=='/api/creation/preview':result=w.creation.preview(d['id'])
            elif self.path=='/api/creation/render':result=w.creation.render(d['id'],d.get('confirmation'),cancel_check=w.cancel_event.is_set)
            elif self.path=='/api/creation/inspect':result=w.creation.inspect(d['id'])
            elif self.path=='/api/creation/draft':result=w.draft('video',w.creation.upload_payload(d['id'],tid=d['tid'],tags=d['tags'],copyright=d['copyright'],source=d.get('source','')))
            elif self.path=='/api/doctor':
                from .doctor import diagnose
                result=diagnose(w.s.root)
            elif self.path=='/api/setup-check':
                from .presets import setup_check
                result=setup_check(w)
            elif self.path=='/api/reconcile':result=w.model.reconcile(d['request_id'],d['cost_micro'],d.get('confirmation'))
            elif self.path=='/api/test-model':result=w.model.test()
            elif self.path=='/api/single-model-test':
                key=d.pop('ephemeral_key',None)
                try:result=w.single_model_test(d.get('request_id'),d.get('confirmation'),d.get('expected'),key)
                finally:key=None
            elif self.path=='/api/login/start':result=w.bili.login_start()
            elif self.path=='/api/login/poll':result=w.bili.login_poll()
            elif self.path=='/api/login/cookie':result=w.bili.save_cookie(d.get('cookie'))
            elif self.path=='/api/account':result=w.bili.login_status(probe=True)
            elif self.path=='/api/info':result=w.read_video_info(d['bvid'])
            elif self.path=='/api/subtitle':result=w.read_video_subtitle(d['bvid'],d.get('refresh',False),d.get('part_index',1))
            elif self.path=='/api/subtitle/search':result=w.subtitle_search.search(d.get('reference'),d.get('part_index'),d.get('query'))
            elif self.path=='/api/subtitle/page':result=w.subtitle_search.page(d.get('reference'),d.get('part_index'),d.get('start',0))
            elif self.path=='/api/comments':result=w.bili.comments(d['bvid'],d.get('page',1))
            elif self.path=='/api/feed':result=w.read_popular()
            elif self.path=='/api/search':result=w.bili.search(d['keyword'])
            elif self.path=='/api/plan':result=w.model.plan(d.get('visual',False))
            elif self.path=='/api/queue/retry':result=w.queue.retry_job(d['key'],upgrade_metadata=d.get('upgrade_metadata',False),confirmation=d.get('confirmation',''))
            elif self.path=='/api/queue/start':result=w.queue.enable(d.get('interval',300))
            elif self.path=='/api/queue/pause':result=w.queue.pause()
            elif self.path=='/api/queue/status':result=w.queue.state()
            elif self.path=='/api/cancel':
                w.queue.pause('主人已取消；需明确重新开启')
                result={'cancelled':True,'queue_paused':True,'note':'已发请求不可撤回；尚未开始的步骤停止，不自动重试'}
            elif self.path=='/api/resume':w.cancel_event.clear();result={'resumed':True}
            elif self.path=='/api/browse':result=w.browse(d['request_id'])
            elif self.path=='/api/analyze':result=w.analyze(d['bvid'],d['request_id'],d.get('mode','subtitle'),d.get('local_video'),part_index=d.get('part_index',1))
            elif self.path=='/api/remember':result=w.remember(d['material_id'])
            elif self.path=='/api/draft':result=w.draft(d['kind'],d['payload'])
            elif self.path=='/api/detail':result=w.draft_detail(d['id'])
            elif self.path=='/api/review':result=w.review(d['id'],d['decision'])
            elif self.path=='/api/send':result=w.execute(d['id'],d.get('confirmation'))
            else:return self.reply(404,{'error':'未知接口'})
            self.reply(200,result)
        except Fault as e:self.reply(400,{'error':str(e)})
        except (ValueError,KeyError,TypeError):self.reply(400,{'error':'参数或服务响应无效'})
        except Exception:self.reply(500,{'error':'操作失败，未自动重试；不确定的外发或费用请人工核对'})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true');parser.add_argument('--single-test',action='store_true');parser.add_argument('--preview',action='store_true');parser.add_argument('--supervised',action='store_true');parser.add_argument('--show-session-link',action='store_true',help='explicitly print local session credential; do not redirect output to shared logs');args=parser.parse_args()
    root=Path(args.data_dir);root.mkdir(parents=True,exist_ok=True)
    from .runtime_lock import RuntimeLock,LockUnavailable
    lock=RuntimeLock(root/'runtime.lock')
    try:lock.acquire()
    except LockUnavailable as e:raise SystemExit(str(e)) from None
    w=None;server=None
    try:
        w=Workbench(root)
        if not args.preview:w.interaction.start_if_enabled()
        server=Server(('127.0.0.1',args.port),w,preview=args.preview);server.supervised=args.supervised and not args.preview and not args.single_test
        from .hot_reload import backend_version
        server.backend_signature=backend_version()
        v=w.s.vault.read();v['web_token']=server.token;v['web_port']=server.server_port;w.s.vault.save(v)
        url=f'http://127.0.0.1:{server.server_port}'+('/single-test' if args.single_test else '/')+'#'+server.token
        print('大肥鱼本机配置页面：http://127.0.0.1:'+str(server.server_port)+'；浏览器自动打开，默认不输出会话口令。',flush=True)
        if args.show_session_link:print('仅本机使用，勿分享或保存到日志：'+url,flush=True)
        if not args.no_browser:webbrowser.open(url)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
    finally:
        if server:server.server_close()
        if w:
            try:
                v=w.s.vault.read()
                if server and v.get('web_token')==server.token:
                    v.pop('web_token',None);v.pop('web_port',None);w.s.vault.save(v)
            except Fault:print('过期本机会话清理失败，请在下次启动时自检')
            finally:w.close()
        lock.release()
    if server and getattr(server,'restart_requested',False) is True:return 75
if __name__=='__main__':raise SystemExit(main() or 0)
