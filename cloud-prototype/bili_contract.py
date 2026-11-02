"""Offline contract for pinned dsh-bili-scroll MCP tools; no process/network transport."""
import json
import re
import copy
import hashlib
from boundary import Subtitle
from dataclasses import dataclass

UPSTREAM_COMMIT='398e4724a481b09b6383a950bcd08723c632da59'
TOOLS=frozenset({'bili_subtitle','bili_video_info'})
BVID=re.compile(r'^BV[0-9A-Za-z]{10}$')

class ContractError(ValueError): pass

def bounded_text(value,limit):
    if not isinstance(value,str) or len(value.encode('utf-8'))>limit: raise ContractError('文本类型或长度无效')
    return value

def optional_text(value,limit):
    return None if value is None else bounded_text(value,limit)

def bounded_int(value,maximum):
    if type(value) is not int or not 0<=value<=maximum: raise ContractError('数值类型或范围无效')
    return value

def optional_int(value,maximum):
    return None if value is None else bounded_int(value,maximum)

@dataclass(frozen=True)
class IsolationProfile:
    isolated: bool = False
    allow_tools: tuple = ()
    def validate(self):
        if self.isolated is not True or len(set(self.allow_tools))!=len(self.allow_tools) or set(self.allow_tools)!=TOOLS:
            raise ContractError('隔离配置未就绪或工具清单不匹配，拒绝启动')

class OfflineFixtureTransport:
    """Fixture is an injected captured-shape response, not an actual MCP connection."""
    def __init__(self,responses):
        self.__responses=copy.deepcopy(responses); self.calls=[]
        canonical=json.dumps(sorted((list(k),v) for k,v in self.__responses.items()),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.revision=hashlib.sha256(canonical.encode()).hexdigest()
    def call(self,method,params):
        self.calls.append((method,params))
        return self.__responses[(params['name'],params['arguments']['bvid'])]

class ReadOnlyBiliContract:
    def __init__(self,profile,transport):
        profile.validate()
        # Prevent treating this educational contract as an approved live bridge.
        if type(transport) is not OfflineFixtureTransport: raise ContractError('仅允许离线 fixture，不支持真实传输')
        self.__transport=transport
        self.revision='dsh-'+UPSTREAM_COMMIT+'-'+transport.revision
    def call(self,name,bvid):
        if name not in TOOLS: raise ContractError('工具未获允许')
        if not isinstance(bvid,str) or not BVID.fullmatch(bvid): raise ContractError('BV号格式无效')
        envelope=self.__transport.call('tools/call',{'name':name,'arguments':{'bvid':bvid}})
        if not isinstance(envelope,dict): raise ContractError('MCP响应格式无效')
        if 'isError' in envelope and type(envelope['isError']) is not bool: raise ContractError('isError须为布尔值')
        if envelope.get('isError') is True: raise ContractError('工具失败')
        content=envelope.get('content')
        if not isinstance(content,list) or len(content)!=1 or not isinstance(content[0],dict) or content[0].get('type')!='text' or not isinstance(content[0].get('text'),str): raise ContractError('MCP内容格式无效')
        if len(content[0]['text'].encode('utf-8'))>1_500_000: raise ContractError('响应过大')
        try: result=json.loads(content[0]['text'])
        except (ValueError,TypeError) as exc: raise ContractError('工具结果不是JSON') from exc
        if not isinstance(result,dict): raise ContractError('结果格式无效')
        if 'error' in result: return {'status':'error','source':'offline fixture','message':bounded_text(result['error'],1000)}
        if name=='bili_subtitle' and 'message' in result: return {'status':'unavailable','source':'offline fixture','message':bounded_text(result['message'],1000)}
        if result.get('bvid')!=bvid: raise ContractError('BV号不匹配')
        if name=='bili_subtitle':
            bounded_text(result.get('text'),1_000_000); bounded_text(result.get('title'),1000); bounded_text(result.get('language'),100); bounded_int(result.get('segments'),100_000)
            return {'status':'ok','source':'offline fixture / bili_subtitle','bvid':bvid,'title':result['title'],'language':result['language'],'segments':result['segments'],'untrusted_subtitle':result['text'],'coverage':'subtitle_only','watched_video':False}
        title=bounded_text(result.get('title'),1000)
        description=optional_text(result.get('description'),100_000)
        author=optional_text(result.get('author'),1000)
        duration=optional_int(result.get('duration'),31_536_000)
        pages=bounded_int(result.get('pages'),100_000)
        stat=result.get('stat')
        names=('view','danmaku','reply','favorite','coin','like','share')
        if not isinstance(stat,dict) or any(key not in stat for key in names): raise ContractError('统计结果不完整')
        safe_stat={key:bounded_int(stat[key],10**15) for key in names}
        return {'status':'ok','source':'offline fixture / bili_video_info','bvid':bvid,'title':title,'description':description,'author':author,'duration':duration,'pages':pages,'stat':safe_stat,'coverage':'metadata_only','watched_video':False}

class BiliSubtitleProvider:
    """Fixture-only adapter bridge for the existing main path; no network."""
    def __init__(self,contract):
        if type(contract) is not ReadOnlyBiliContract: raise ContractError('非法合同实例')
        self.__contract=contract; self.revision=contract.revision
    def get_subtitle(self,video_id):
        result=self.__contract.call('bili_subtitle',video_id)
        if result['status']!='ok': raise ContractError('字幕不可用：'+result.get('message','unknown'))
        metadata=self.__contract.call('bili_video_info',video_id)
        if metadata['status']!='ok': raise ContractError('元数据不可用：'+metadata.get('message','unknown'))
        return Subtitle(video_id,result['untrusted_subtitle'],result['source']+' @ '+UPSTREAM_COMMIT,metadata=metadata)
