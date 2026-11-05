"""Endpoints from provider documentation; models and prices require owner choice."""
from .runtime import Fault
PRESETS={
    'aliyun-beijing':{'provider_url':'https://{workspace_id}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1','model':'','vision_model':'','price_version':'','paid_enabled':False,'daily_budget_micro':0,'request_cap_micro':0},
    'deepseek':{'provider_url':'https://api.deepseek.com','model':'','vision_model':'','price_version':'','paid_enabled':False,'daily_budget_micro':0,'request_cap_micro':0},
}
def apply_preset(state,name,workspace_id=None):
    if name not in PRESETS:raise Fault('未知提供方预设')
    preset=dict(PRESETS[name],input_price_micro_per_million=0,output_price_micro_per_million=0,vision_input_price_micro_per_million=0,vision_output_price_micro_per_million=0,vision_price_version='')
    if name=='aliyun-beijing':
        import re
        if not isinstance(workspace_id,str) or not re.fullmatch('[A-Za-z0-9-]{1,100}',workspace_id):raise Fault('请从阿里云控制台填写工作空间ID，以官方当前接口为准')
        preset['provider_url']=preset['provider_url'].format(workspace_id=workspace_id)
    return state.configure(preset)

def setup_check(workbench):
    import shutil
    c=workbench.s.config();plan=workbench.model.plan();login=workbench.bili.login_status();issues=list(plan['missing'])
    if not c['read_enabled']:issues.append('允许平台读取')
    if workbench.qq.config()['acceptance_owner_only']:issues.append('当前仅主人私聊验收，未启用视频推理')
    if login['credential_present'] and not login.get('verified'):issues.append('本地已有登录态但尚未验证/已失效，点击检查独立账号')
    return {'ready_for_configured_text_path':not issues,'missing':issues,'model_plan':plan,'login_status':login,'anonymous_read_allowed':c['read_enabled'] and not login['credential_present'],'visual_dependencies_present':bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),'requires_live_test':['Windows/Mac凭据保护与启动','独立账号首次扫码和登录态有效性','模型实际推理与用量','平台外发回执'],'no_guessed_credentials':True,'price_warning':'只按配置估算，不是实际账单；预设不猜模型、价格、地区和预算；无字幕内容可能需独立账号或显式视觉回退'}
