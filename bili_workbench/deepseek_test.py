"""One-off owner acceptance preparation, not an automatic paid test.
Official pricing checked 2026-11-03; snapshot must be rechecked before reuse.
"""
from datetime import datetime,timezone
from urllib.parse import urlsplit
from .runtime import Fault

NOT_BEFORE=datetime(2026,11,3,10,tzinfo=timezone.utc)  # 18:00 Asia/Shanghai
SOURCE='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
SNAPSHOT={'checked_date':'2026-11-03','currency':'CNY','unit':'per million tokens','flash':{'model':'deepseek-flash','vision_supported':True,'off_peak_input_cache_miss':1,'off_peak_input_cache_hit':0.02,'off_peak_output':4,'peak_input_cache_miss':2,'peak_input_cache_hit':0.04,'peak_output':8},'pro':{'model':'deepseek-v4-pro','vision_supported':False,'off_peak_input_cache_miss':4.5,'off_peak_input_cache_hit':0.15,'off_peak_output':13.5,'peak_input_cache_miss':9,'peak_input_cache_hit':0.30,'peak_output':27},'peak_hours_beijing':'周一至周五09:00–12:00、14:00–18:00，不含中国法定节假日；其余低谷','source':SOURCE}

class OffPeakDeferred(Fault):
    def __init__(self,detail):
        self.detail=detail
        super().__init__('DeepSeek等待已授权的低谷时段；未发出模型请求')

def utc_now():return datetime.now(timezone.utc)
def window(now=None):
    from .tariff import status
    now=now or utc_now()
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:raise Fault('测试时间必须含时区')
    tariff=status(now);first_allowed=now.astimezone(timezone.utc)>=NOT_BEFORE
    resume=NOT_BEFORE.isoformat() if not first_allowed else tariff['resume_at_utc']
    return {'not_before_utc':NOT_BEFORE.isoformat(),'not_before_beijing':'2026-11-03T18:00:00+08:00','time_allowed':first_allowed and tariff['allowed'],'resume_at_utc':resume,'defer_reason':'first_test_not_before' if not first_allowed else tariff['reason'],'tariff':tariff,'automatic_execution':False,'scope':'仅准备单一主人私聊验收，真实消息仍未授权发送','price_snapshot':SNAPSHOT,'note':'每次模型调用前重新检查；到点不自动调用；QQ+B站共享明确配置的额度，目标约10元/天但默认预算0'}

def official_endpoint(url):
    u=urlsplit(url)
    return u.scheme=='https' and u.hostname=='api.deepseek.com' and u.port in (None,443) and not u.username and not u.password

def require_window(url):
    if official_endpoint(url):
        detail=window()
        if not detail['time_allowed']:raise OffPeakDeferred(detail)

def provider_options(url,model):
    # The official API defaults to thinking. For the small acceptance test,
    # disable it explicitly; never offer search, payment or social tools.
    return {'thinking':{'type':'disabled'},'tool_choice':'none'} if official_endpoint(url) and model in ('deepseek-flash','deepseek-v4-pro') else {}

def prepare(workbench):
    state=workbench.s
    # User still supplies Key and explicit cap/budget locally. Do not touch Vault.
    config={'provider_url':'https://api.deepseek.com','model':'deepseek-flash','vision_model':'','paid_enabled':False,'daily_budget_micro':0,'request_cap_micro':0,'currency':'CNY','budget_day_timezone':'Asia/Shanghai','price_version':'deepseek-official-20261103-CNY-peak-upper-bound','input_price_micro_per_million':2000000,'output_price_micro_per_million':8000000,'max_input_tokens':2048,'max_output_tokens':128,'vision_price_version':'','vision_input_price_micro_per_million':0,'vision_output_price_micro_per_million':0,'outbound_enabled':False,'qq_auto_process':False,'qq_auto_send':False,'qq_sender_enabled':False}
    state.configure(config)
    workbench.qq.configure({'acceptance_owner_only':True})
    return {'prepared':True,'network_called':False,'credentials_created':False,'key_required_locally':True,'paid_enabled':False,'daily_budget_micro':0,'daily_target_micro':10000000,'request_cap_micro':0,'expected_max_per_call_micro_at_configured_ceiling':5120,'cost_basis':'高峰缓存未命中上界估算，不是实际账单；低谷实际价格较低，缓存命中也可能更低','time_window':window(),'live_qq_ready':False,'missing_transport':'当前仅fixture草稿；须本机验证SnowLuma认证、事件与发送回执后才可真实回复','scope':'本次仅主人私聊，不修改已获准的日常社交策略'}
