"""Offline plan only: no pricing assumptions, no model usage or network."""
def plan_video(mode, estimate_inputs=None):
    stages={'字幕':['检查提供的字幕','本地字幕解析或低成本文本模型（待授权）'],
            '画面':['检查提供的字幕','按需提取关键帧（未实现）','视觉模型（待授权）'],
            '难例':['检查提供的字幕','按需关键帧（未实现）','升级模型（待授权）']}
    if mode not in stages: raise ValueError('请选择 字幕、画面 或 难例')
    cost=None; reason='未配置提供方、价格和用量，无法估算'
    if estimate_inputs is not None:
        if not isinstance(estimate_inputs,(tuple,list)) or len(estimate_inputs)!=4 or any(type(x) is not int or x<0 or x>10**12 for x in estimate_inputs): raise ValueError('估算参数须为四个非负整数')
        tin,tout,pin,pout=estimate_inputs
        cost=(tin*pin+tout*pout+999999)//1000000
        reason='基于用户输入的假设，微货币单位，向上取整；不是报价'
    return {'stages':stages[mode], 'coverage':'尚未阅读', 'external_authorized':False, 'paid_budget':0,
            'estimate_micro':cost,'estimate_reason':reason,'estimate_inputs':estimate_inputs,
            'actual_usage':None,'actual_cost':None,'note':'仅计划；未调用模型或平台'}
