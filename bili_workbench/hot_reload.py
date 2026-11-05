"""Conservative checks before a user-requested supervised restart."""

def backend_version():
    import hashlib
    from pathlib import Path
    root=Path(__file__).parent;h=hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        h.update(path.name.encode('utf8')+b'\0');h.update(path.read_bytes())
    return h.hexdigest()

def blocking_reason(workbench):
    db=workbench.s.db
    checks=(
        ('charges',"state IN ('reserved','uncertain')",'有进行中或待核对的费用'),
        ('local_chat_requests',"state='running'",'本机对话正在处理'),
        ('drafts',"state='sending'",'有正在发送的草稿'),
        ('browse_control',"enabled=1",'连续浏览仍在运行'),
        ('qq_native_inbox',"state='processing'",'QQ 消息正在处理'),
        ('qq_transport_outbox',"state='sending'",'QQ 草稿正在发送'),
        ('public_activity_tasks',"state='running'",'公开创作任务正在运行'),
        ('single_model_tests',"state='started'",'一次性模型测试正在进行或待核对'),
        ('proactive_attempts',"state='running'",'主动互动正在处理'),
        ('computer_action_drafts',"state='running'",'电脑操作正在处理'),
    )
    for table,condition,reason in checks:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            if db.execute('SELECT 1 FROM '+table+' WHERE '+condition+' LIMIT 1').fetchone():return reason
    if workbench.s.config().get('qq_auto_process'):return 'QQ 后台自动处理已开启'
    activity=getattr(workbench,'system_activity',None)
    if activity:
        status=activity.status()
        if status['running'] or status['inflight']:return '电脑活动监测或主动互动仍在运行'
    return None
