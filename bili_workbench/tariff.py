"""DeepSeek inference clock only; no pricing assumptions for Search/Fetch/others.
Beijing UTC+8 is explicit, so Windows needs no downloaded tzdata package.
"""
from datetime import datetime,timedelta,timezone
from .runtime import Fault
BJ=timezone(timedelta(hours=8))
CALENDAR_SOURCE='https://www.wuhan.gov.cn/zwgk/tzgg/202609/t20260907_2844002.shtml'
ANNOUNCED_2026=(('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),('2026-06-19','2026-06-21'),('2026-09-25','2026-09-27'),('2026-10-01','2026-10-07'))
# Provider does not expose a verified day-by-day billing holiday calendar here.
# Full government rest periods can include adjusted rest days. At potential
# peak hours these dates stay unconfirmed, never guessed to be discounted.
PROVIDER_CONFIRMED_HOLIDAYS=frozenset()

def status(now):
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:raise Fault('计价时钟须含时区')
    local=now.astimezone(BJ);day=local.date().isoformat();minute=local.hour*60+local.minute
    potential=(540<=minute<720) or (840<=minute<1080)
    if local.weekday()>=5:reason='weekend';allowed=True
    elif not potential:reason='off_peak';allowed=True
    elif day in PROVIDER_CONFIRMED_HOLIDAYS:reason='verified_holiday';allowed=True
    elif local.year!=2026:reason='calendar_unknown';allowed=False
    elif any(start<=day<=end for start,end in ANNOUNCED_2026):reason='holiday_unconfirmed';allowed=False
    else:reason='peak';allowed=False
    resume=local
    if not allowed:resume=local.replace(hour=12 if minute<720 else 18,minute=0,second=0,microsecond=0)
    return {'allowed':allowed,'reason':reason,'beijing_time':local.isoformat(),'resume_at_utc':resume.astimezone(timezone.utc).isoformat(),'calendar_source':CALENDAR_SOURCE,'holiday_rule':'周末全天按官方低谷规则；提供方未逐日确认的调休/节假日及未知年份，在可能高峰时段保守暂停','applies_to':'DeepSeek模型token推理；不包含其他供应商或Search/Fetch'}
