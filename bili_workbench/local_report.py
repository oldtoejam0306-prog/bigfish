"""Explicitly requested summaries of BigFish's own local records only."""
from datetime import datetime, timedelta, timezone
from .runtime import Fault


def activity_report(workbench, period, now=None):
    if period not in ('day', 'week'): raise Fault('报告周期无效')
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None: raise Fault('报告时间必须包含时区')
    local = current.astimezone(timezone(timedelta(hours=8)))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'week': start -= timedelta(days=local.weekday())
    # The end is now, not a future interval, and all timestamps are UTC seconds.
    begin, end = start.timestamp(), current.timestamp()
    with workbench.s.lock:
        chat = workbench.s.db.execute("SELECT COUNT(*) FROM local_chat_requests WHERE state='completed' AND created>=? AND created<=?", (begin,end)).fetchone()[0]
        account = workbench.s.config()['account_mid'] or 'anonymous'
        bili = workbench.s.db.execute('SELECT COUNT(*) FROM manual_video_reads WHERE account=? AND created>=? AND created<=?', (account,begin,end)).fetchone()[0]
    return {'period':period,'start':start.isoformat(),'through':local.isoformat(),'timezone':'UTC+08:00',
            'local_chat_completed':chat,'bili_manual_read_records':bili,
            'scope':'仅统计大肥鱼已有本机记录，不监控屏幕、键盘或其他软件；视频数为新记录数，不代表观看时长。'}
