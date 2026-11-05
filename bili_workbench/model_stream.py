"""Bounded, cancellable DeepSeek SSE transport. No network starts at import.

Only the verified official endpoint/model pair is enabled. Text is buffered for
the existing full-response privacy checks; streaming transport is not streaming
speech or permission to publish unchecked partial text. Missing final usage or
interruption remains an uncertain charge in Model.complete.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .runtime import Fault,canonical


class StreamCancelled(Fault):
    def __init__(self):super().__init__('模型流已取消；已发出的请求可能仍计费')


class StreamControl:
    def __init__(self):self.event=threading.Event();self.lock=threading.Lock();self.attempted=False
    def cancel(self):self.event.set()
    def cancelled(self):return self.event.is_set()
    def check(self):
        if self.cancelled():raise StreamCancelled()
    def begin_request(self):
        with self.lock:self.check();self.attempted=True


def supported(url,model):
    from .deepseek_test import official_endpoint
    return official_endpoint(url) and urllib.parse.urlsplit(url).path.rstrip('/') in ('','/v1') and model in ('deepseek-flash','deepseek-v4-pro')


def _events(response,control,*,max_bytes=1_000_000,deadline=None):
    """Read small network blocks, including when an SSE line is incomplete."""
    fields=[];size=0;total=0;first=True;pending=bytearray()
    while True:
        control.check()
        if deadline is not None and time.monotonic()>deadline:raise Fault('模型流超过总时限')
        block=response.read1(4096)
        if not block:raise Fault('模型流未取得明确终止事件')
        if type(block) is not bytes or len(block)>4096:raise Fault('模型流读取格式无效')
        total+=len(block)
        if total>max_bytes:raise Fault('模型流超过大小限制')
        pending.extend(block)
        while b'\n' in pending:
            control.check();end=pending.index(10);raw=bytes(pending[:end+1]);del pending[:end+1]
            if len(raw)>65536:raise Fault('模型流行过大')
            try:line=raw.decode('utf8').rstrip('\r\n')
            except UnicodeError:raise Fault('模型流编码无效') from None
            if first:line=line.removeprefix('\ufeff');first=False
            if not line:
                if fields:yield '\n'.join(fields);fields=[];size=0
                continue
            if line.startswith(':'):continue
            name,sep,value=line.partition(':')
            if value.startswith(' '):value=value[1:]
            if name=='data':
                size+=len(raw)
                if size>65536:raise Fault('模型流事件过大')
                fields.append(value)
            elif name=='event' and value not in ('','message'):raise Fault('模型流返回非文本事件')
            elif name not in ('event','id','retry'):raise Fault('模型流字段无效')
        if len(pending)>65536:raise Fault('模型流行过大')


def receive(response,control,*,deadline=None):
    parts=[];text_bytes=0;usage=None;finished=False;ident=None
    for event in _events(response,control,deadline=deadline):
        control.check()
        if event=='[DONE]':
            if not finished or not isinstance(usage,dict):raise Fault('模型流缺少结束状态或真实用量')
            return {'choices':[{'message':{'content':''.join(parts)}}],'usage':usage,'transport_streaming':True}
        try:item=json.loads(event)
        except (ValueError,TypeError):raise Fault('模型流JSON无效') from None
        if not isinstance(item,dict) or item.get('error') is not None:raise Fault('模型流返回错误')
        current=item.get('id')
        if current is not None:
            if not isinstance(current,str) or not current or (ident is not None and current!=ident):raise Fault('模型流响应标识变化')
            ident=current
        choices=item.get('choices')
        if not isinstance(choices,list) or len(choices)>1:raise Fault('模型流只接受单一文本候选')
        incoming=item.get('usage')
        if incoming is not None:
            if not isinstance(incoming,dict) or usage is not None:raise Fault('模型流用量无效或重复')
            usage=incoming
        if not choices:
            if not finished or incoming is None:raise Fault('模型流空候选无效')
            continue
        choice=choices[0]
        if not isinstance(choice,dict) or type(choice.get('index')) is not int or choice['index']!=0:raise Fault('模型流候选序号无效')
        delta=choice.get('delta')
        if not isinstance(delta,dict) or delta.get('role','assistant')!='assistant' or delta.get('tool_calls') or delta.get('function_call') or delta.get('reasoning_content'):raise Fault('模型流包含非预期工具或推理内容')
        content=delta.get('content')
        if content is not None:
            if not isinstance(content,str) or finished:raise Fault('模型流文本顺序无效')
            text_bytes+=len(content.encode('utf8'))
            if text_bytes>100000:raise Fault('模型流文本过大')
            parts.append(content)
        reason=choice.get('finish_reason')
        if reason is not None:
            if reason!='stop' or finished:raise Fault('模型流未正常完成')
            finished=True
        if incoming is not None and not finished:raise Fault('模型流用量出现在终止状态前')
    raise Fault('模型流未完成')


def request(opener,url,headers,payload,control):
    if not isinstance(control,StreamControl):raise Fault('模型流取消控制无效')
    if not supported(url.rsplit('/chat/completions',1)[0],payload.get('model')):raise Fault('尚未验证该模型接口的流式协议')
    body=dict(payload,stream=True,stream_options={'include_usage':True})
    h=dict(headers);h.update({'Content-Type':'application/json','Accept':'text/event-stream'})
    req=urllib.request.Request(url,data=canonical(body).encode('utf8'),headers=h,method='POST')
    control.begin_request()
    try:
        # One read may wait up to 15s. Cancellation is cooperative at read
        # boundaries; the caller must keep reporting pending until this exits.
        with opener.open(req,timeout=15) as response:
            if response.headers.get_content_type()!='text/event-stream':raise Fault('模型未返回SSE；不自动回退重试')
            return receive(response,control,deadline=time.monotonic()+120)
    except urllib.error.HTTPError as error:
        error.close();raise Fault('模型流HTTP失败；不自动重试') from None
    except (urllib.error.URLError,TimeoutError,OSError):raise Fault('模型流中断或费用不确定；不自动重试') from None
