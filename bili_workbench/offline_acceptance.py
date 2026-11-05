"""Explicit credential-free, network-denied acceptance and read-only preview.

This entry does not call web.main, does not exercise production Vault/DPAPI,
and never migrates an existing workbench profile. The default is a bounded
check with no browser. Opening a browser requires both explicit flags.
"""
import argparse
import json
import os
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from .runtime import Fault
from .runtime_lock import RuntimeLock
from .service import Workbench
from .web import Server


class NoProviderHTTP:
    def __init__(self):self.attempts=0
    def request(self,*args,**kwargs):
        self.attempts+=1;raise Fault('离线验收不访问平台或模型')
    def json(self,*args,**kwargs):return self.request(*args,**kwargs)


class EmptyPreviewVault:
    def read(self):return {}
    def save(self,data):raise Fault('离线验收不保存凭据或会话口令')


class NoDNSLocalServer(Server):
    def server_bind(self):
        # HTTPServer normally does a reverse lookup here. Offline mode never
        # asks DNS, including while constructing its listener.
        socketserver.TCPServer.server_bind(self)
        self.server_name='127.0.0.1';self.server_port=self.server_address[1]


class Boundary:
    def __init__(self,allow_browser=False):
        self.port=None;self.browser_url=None;self.allow_browser=allow_browser
        self.phase='initializing';self.started=False;self.stopped=False
        self.loopback_connections=0;self.external_attempts=0;self.credential_file_attempts=0;self.process_attempts=0;self.native_library_attempts=0;self.browser_attempts=0
        self.temp=Path(tempfile.gettempdir()).resolve()
    def audit(self,event,args):
        if event=='socket.connect':
            address=args[1]
            if not isinstance(address,tuple) or address[0]!='127.0.0.1' or address[1]!=self.port:
                self.external_attempts+=1;raise PermissionError('offline boundary: no external or other local service connections')
            self.loopback_connections+=1
        elif event=='socket.getaddrinfo':
            if args[0]!='127.0.0.1' or args[1]!=self.port:
                self.external_attempts+=1;raise PermissionError('offline boundary: no DNS')
        elif event in ('socket.gethostbyname','socket.gethostbyaddr','socket.sendto'):
            self.external_attempts+=1;raise PermissionError('offline boundary: no DNS or datagrams')
        elif event=='socket.bind':
            address=args[1]
            if not isinstance(address,tuple) or address[0]!='127.0.0.1' or address[1]!=0:
                self.external_attempts+=1;raise PermissionError('offline boundary: fresh loopback listener only')
        elif event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
            if Path(os.fsdecode(args[0])).name.lower() in ('secrets.bin','secrets.tmp'):
                self.credential_file_attempts+=1;raise PermissionError('offline boundary: credential files forbidden')
        elif event in ('subprocess.Popen','os.system'):
            self.process_attempts+=1;raise PermissionError('offline boundary: subprocesses forbidden')
        elif event=='ctypes.dlopen':
            self.native_library_attempts+=1;raise PermissionError('offline boundary: device and credential library calls forbidden')
        elif event.startswith('os.startfile'):
            self.browser_attempts+=1
            if not self.allow_browser or args[0]!=self.browser_url:raise PermissionError('offline boundary: browser not requested')
        elif event=='shutil.rmtree':
            target=Path(args[0]).resolve()
            if target.parent!=self.temp or not target.name.startswith(('bigfish-offline-v13-','bigfish-preflight-')):
                raise PermissionError('offline boundary: cleanup outside owned temporary directory')


def inspect_service(server,workbench):
    base='http://127.0.0.1:'+str(server.server_port)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));checks={}
    def get(path,auth=True,json_body=True):
        request=urllib.request.Request(base+path,headers={'X-Local-Token':server.token} if auth else {})
        with opener.open(request,timeout=4) as response:
            raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise AssertionError('bounded response exceeded')
            if response.headers.get('Cache-Control')!='no-store':raise AssertionError('cache policy mismatch')
            return json.loads(raw) if json_body else raw.decode('utf8')
    page=get('/',auth=False,json_body=False)
    checks['actual_readonly_page_served']='data-preview="true"' in page and '大肥鱼' in page
    assets=['/duplex-client.js','/duplex-audio.js','/report-view.js','/sticker-chat.js','/local-recovery.js']
    checks['all_five_browser_modules_served']=all(bool(get(path,auth=False,json_body=False)) for path in assets)
    try:get('/api/local-chat/history',auth=False)
    except urllib.error.HTTPError as error:
        error.read();error.close();checks['unauthorized_private_reads_refused']=error.code==403
    for period in ('day','week'):
        report=get('/api/activity-report/'+period)
        checks['empty_'+period+'_report']=report['local_chat_completed']==0 and report['bili_manual_read_records']==0
    checks['role_recovery_ready']=get('/api/local-chat/recovery/current')['character_id']=='default' and get('/api/local-chat/recovery/default/pending')['items']==[]
    checks['chat_and_sticker_reads_ready']=get('/api/local-chat/history')['messages']==[] and get('/api/stickers')['items']==[]
    checks['activity_sampling_stopped']=get('/api/system-activity/status')['running'] is False
    voice=get('/api/voice/readiness');checks['no_native_voice_probe']=voice['ready'] is False and voice['checked'] is False
    expression=get('/api/owner-expression');checks['expression_defaults_safe']=expression['stickers'] is True and expression['emotional_voice'] is True and all(expression[key] is False for key in ('qq_media_enabled','qq_media_validated','native_voice_authorized'))
    version=get('/api/ui-version');checks['version_endpoint_ready']=len(version['version'])==64 and version['supervised'] is False
    checks['empty_vault_status_ready']=get('/api/status')['bili_login']['state']=='not_configured'
    payload=json.dumps({'request_id':'offline-not-run-01','text':'离线检查不会提交模型'}).encode()
    request=urllib.request.Request(base+'/api/local-chat/send',data=payload,headers={'Content-Type':'application/json','X-Local-Token':server.token})
    try:opener.open(request,timeout=4)
    except urllib.error.HTTPError as error:
        error.read();error.close();checks['all_writes_refused_in_preview']=error.code==403
    config=workbench.s.config()
    checks['zero_budget_and_outbound_disabled']=config['paid_enabled'] is False and config['daily_budget_micro']==0 and config['request_cap_micro']==0 and config['outbound_enabled'] is False
    checks['no_model_requests_recorded']=workbench.s.db.execute('SELECT COUNT(*) FROM local_chat_requests').fetchone()[0]==0 and workbench.s.db.execute('SELECT COUNT(*) FROM charges').fetchone()[0]==0
    return checks


def run(boundary,*,preview=False,open_browser=False):
    code_root=Path(__file__).resolve().parents[1];server=None;thread=None;workbench=None;lock=None
    checks={};started=False;stopped=False;transport=NoProviderHTTP();preflight_result=None;integrity=None
    with tempfile.TemporaryDirectory(prefix='bigfish-offline-v13-') as folder:
        data=Path(folder).resolve()
        if data.parent!=boundary.temp or not data.name.startswith('bigfish-offline-v13-'):raise RuntimeError('unexpected temporary data directory')
        try:
            boundary.phase='creating_fresh_workbench'
            lock=RuntimeLock(data/'runtime.lock').acquire()
            workbench=Workbench(data,transport);workbench.s.vault=EmptyPreviewVault()
            server=NoDNSLocalServer(('127.0.0.1',0),workbench,preview=True);boundary.port=server.server_port
            from .hot_reload import backend_version
            server.backend_signature=backend_version()
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();started=True;boundary.started=True;boundary.phase='checking_loopback_routes'
            checks=inspect_service(server,workbench)
            from .doctor import source_check
            integrity=source_check();checks['packaged_source_hashes_match']=integrity['state']=='matches'
            if preview:
                if not all(checks.values()):raise RuntimeError('offline preview checks failed')
                print('大肥鱼 v13 离线只读预览；使用全新临时数据，不输入 Key。关闭此窗口或按 Ctrl+C 结束。',flush=True)
                if open_browser:
                    import webbrowser
                    boundary.browser_url='http://127.0.0.1:'+str(server.server_port)+'/#'+server.token
                    if not webbrowser.open(boundary.browser_url):print('浏览器未能确认打开；服务仍在本机运行，不输出会话口令。',flush=True)
                try:
                    while thread.is_alive():thread.join(timeout=0.5)
                except KeyboardInterrupt:pass
            else:
                boundary.phase='offline_feature_preflight'
                from .preflight import run as preflight
                preflight_result=preflight();checks['existing_offline_feature_preflight']=preflight_result['passed'] is True
        finally:
            if server and thread:
                server.shutdown();thread.join(timeout=5);stopped=not thread.is_alive();boundary.stopped=stopped;server.server_close()
            if workbench:workbench.close()
            if lock:lock.release()
        checks['no_credential_file_created']=not (data/'secrets.bin').exists() and not (data/'secrets.tmp').exists()
    passed=started and stopped and bool(checks) and all(checks.values()) and transport.attempts==0 and boundary.external_attempts==0 and boundary.credential_file_attempts==0 and boundary.process_attempts==0 and boundary.native_library_attempts==0 and ((preview and open_browser) or boundary.browser_attempts==0)
    return {'passed':passed,'code_root':str(code_root),'actual_local_service_started':started,'actual_local_service_stopped':stopped,'checks':checks,'source_integrity':integrity,'offline_feature_preflight':preflight_result,'external_connections':0,'external_attempts':boundary.external_attempts,'own_loopback_connections':boundary.loopback_connections,'provider_attempts':transport.attempts,'credential_file_attempts':boundary.credential_file_attempts,'subprocess_attempts':boundary.process_attempts,'native_library_attempts':boundary.native_library_attempts,'browser_open_attempts':boundary.browser_attempts,'data':'fresh owned temporary directory, removed after stop','credential_mode':'explicit empty in-memory preview store; real Vault/DPAPI not tested','normal_workbench_main_started':False,'microphone_or_screen_capture_started':False,'real_credentials_used':False,'paid_models_called':False,'real_platform_messages_sent':False,'visual_browser_acceptance':False}


def main():
    parser=argparse.ArgumentParser(description='Default: bounded offline acceptance; no browser or credential store')
    parser.add_argument('--preview',action='store_true');parser.add_argument('--open-browser',action='store_true');args=parser.parse_args()
    if args.open_browser and not args.preview:parser.error('--open-browser requires explicit --preview')
    boundary=Boundary(allow_browser=args.open_browser);sys.addaudithook(boundary.audit)
    try:result=run(boundary,preview=args.preview,open_browser=args.open_browser)
    except Exception as error:
        # Do not expose tokens, response bodies, user paths from errors or keys.
        result={'passed':False,'error_type':type(error).__name__,'phase':boundary.phase,'actual_local_service_started':boundary.started,'actual_local_service_stopped':boundary.stopped,'no_retry':True,'normal_workbench_main_started':False,'external_attempts':boundary.external_attempts,'credential_file_attempts':boundary.credential_file_attempts,'subprocess_attempts':boundary.process_attempts,'native_library_attempts':boundary.native_library_attempts,'browser_open_attempts':boundary.browser_attempts}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
