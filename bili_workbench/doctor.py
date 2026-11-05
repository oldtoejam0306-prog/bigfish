"""Local deployment diagnosis: never decrypts Vault, calls APIs or edits data."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import shutil
import sys
from pathlib import Path

def source_check():
    root=Path(__file__).resolve().parents[1];manifest=root/'BILI-SOURCE-MANIFEST.json'
    if not manifest.is_file():return {'state':'manifest_missing','checked':0,'not_a_security_audit':True}
    try:
        data=json.loads(manifest.read_text(encoding='utf-8'));entries=data['files']
        if not isinstance(entries,list) or len(entries)>1000:raise ValueError()
        changed=[]
        for item in entries:
            name=item['path'];relative=Path(name)
            if not isinstance(name,str) or relative.is_absolute() or '..' in relative.parts or not relative.parts:raise ValueError()
            file=root/relative;resolved=file.resolve()
            if not resolved.is_relative_to(root) or file.is_symlink() or file.suffix in ('.db','.bin','.log','.env') or any(p in ('data','.git','__pycache__') for p in relative.parts):raise ValueError()
            if not file.is_file() or file.stat().st_size>2000000 or hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:changed.append(name)
        return {'state':'matches' if not changed else 'changed','checked':len(entries),'changed_paths':changed,'not_a_security_audit':True}
    except (OSError,ValueError,TypeError,KeyError):return {'state':'manifest_invalid','checked':0,'not_a_security_audit':True}

def diagnose(root,*,probe_media=False,verify_source=False):
    root=Path(root).expanduser().resolve();commands={name:bool(shutil.which(name)) for name in ('ffmpeg','ffprobe','node')}
    report={'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,10),'platform':sys.platform,'sqlite':sqlite3.sqlite_version,'data_directory_exists':root.is_dir(),'credentials_file_present':(root/'secrets.bin').is_file(),'credentials_read':False,'network_called':False,'messages_sent':0,'data_changed':False,'commands':commands,'node_optional':True,'windows_mac_actual_acceptance':False,'lock_present':(root/'runtime.lock').exists(),'lock_live_process_verified':False,'database':{'state':'not_created'}}
    dbfile=root/'workbench.db'
    if dbfile.is_file():
        db=None
        try:
            db=sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            config=json.loads(db.execute('SELECT value FROM cfg WHERE id=1').fetchone()[0]) if 'cfg' in tables else {}
            if not isinstance(config,dict):raise ValueError()
            database={'state':'readable','provider_configured':bool(config.get('provider_url') and config.get('model')),'explicit_budget_configured':bool(config.get('daily_budget_micro') and config.get('request_cap_micro')),'paid_flag':config.get('paid_enabled') is True,'qq_endpoint_configured':bool(config.get('qq_endpoint')),'qq_sender_enabled':config.get('qq_sender_enabled') is True,'qq_transport_claimed_validated':config.get('qq_transport_validated') is True,'bili_read_enabled':config.get('read_enabled') is True}
            database['unresolved_charges']=db.execute("SELECT count(*) FROM charges WHERE state IN ('reserved','uncertain')").fetchone()[0] if 'charges' in tables else 0
            database['uncertain_qq_sends']=db.execute("SELECT count(*) FROM qq_transport_outbox WHERE state IN ('sending','uncertain')").fetchone()[0] if 'qq_transport_outbox' in tables else 0
            database['uncertain_bili_writes']=db.execute("SELECT count(*) FROM drafts WHERE state IN ('sending','uncertain')").fetchone()[0] if 'drafts' in tables else 0
            report['database']=database
        except (sqlite3.Error,ValueError,TypeError,KeyError):report['database']={'state':'unreadable_or_incompatible','note':'不回显内容；不迁移/修复或删除账本'}
        finally:
            if db:db.close()
    if probe_media:
        media={'checked':False,'network_called':False,'encoders':{'libx264':False,'mov_text':False}}
        if commands['ffmpeg']:
            try:
                completed=subprocess.run([shutil.which('ffmpeg'),'-hide_banner','-encoders'],capture_output=True,timeout=10,check=True)
                if len(completed.stdout)>1000000:raise ValueError()
                text=completed.stdout.decode('utf-8',errors='replace')
                media={'checked':True,'network_called':False,'encoders':{key:any(key in line.split() for line in text.splitlines()) for key in ('libx264','mov_text')}}
            except (OSError,ValueError,subprocess.SubprocessError):media['note']='本机编码器检查失败，不安装或输出原始stderr'
        report['media']=media
    if verify_source:report['source_integrity']=source_check()
    report['requires_live_acceptance']=['QQ实际桥协议/账号/签名与回执','独立B站账号和远端画面','模型真实推理/usage/提供方账单','Windows/Mac凭据保护与启动']
    return report

def main():
    parser=argparse.ArgumentParser(description='No-secret, no-network deployment doctor')
    parser.add_argument('--data-dir',required=True);parser.add_argument('--probe-media',action='store_true');parser.add_argument('--verify-source',action='store_true');args=parser.parse_args()
    result=diagnose(args.data_dir,probe_media=args.probe_media,verify_source=args.verify_source)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['python_supported'] or result['database']['state']=='unreadable_or_incompatible':raise SystemExit(1)
if __name__=='__main__':main()
