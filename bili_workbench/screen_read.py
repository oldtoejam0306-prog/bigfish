"""Explicit, one-shot owner-authorized screenshot analysis; no capture API."""
import base64
import binascii
import re
from .growth import Scope
from .runtime import Fault


def analyze_screenshot(workbench,request_id,jpeg_base64,question,confirmation):
    if confirmation!='确认发送当前画面并分析':raise Fault('需要逐次确认发送当前画面给模型')
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id):raise Fault('请求编号无效')
    if not isinstance(question,str) or not question.strip() or len(question)>500:raise Fault('请输入不超过 500 字的问题')
    if not isinstance(jpeg_base64,str) or len(jpeg_base64)>800000:raise Fault('画面大小无效')
    try:raw=base64.b64decode(jpeg_base64,validate=True)
    except (ValueError,binascii.Error):raise Fault('画面编码无效')
    if not 4<=len(raw)<=600000 or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):raise Fault('画面格式无效')
    c=workbench.s.config()
    if not c['paid_enabled'] or not c['daily_budget_micro'] or not c['request_cap_micro']:raise Fault('付费功能未启用或预算为零；没有发送画面')
    active=workbench.characters.current()
    context=workbench.growth.context(Scope.owner(),include_memory=False,max_turns=0,source_prefix='local-owner:')
    conversation={'audience':'owner_private','channel':'screen'}
    if active['id']!='default':
        context['personality']={'version':1,'data':{'name':active['name'],'identity':active['identity'],'traits':active['traits']}}
        conversation['character_kind']='custom'
    result=workbench.model.chat(question.strip(),'screen:'+request_id,context['personality'],[],conversation=conversation,history=[],images=['data:image/jpeg;base64,'+jpeg_base64],private_uncached=True)
    return {'text':result['text'],'coverage':'单张当前画面','model_called':result.get('model_called',not result.get('cached',False)),'automatically_retried':False,'image_persisted':False,'result_cached':False}
