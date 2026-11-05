"""Live Bili response guards reuse the existing pinned-contract scalar guards."""
import math
from bili_contract import bounded_text,bounded_int,ContractError
from .runtime import Fault

def view(data,bvid):
    try:
        if not isinstance(data,dict) or data.get('bvid')!=bvid:raise ContractError('视频身份不匹配')
        safe={'bvid':bvid,'aid':bounded_int(data.get('aid'),2**63-1),'cid':bounded_int(data.get('cid'),2**63-1),'title':bounded_text(data.get('title'),2000)}
        if not safe['aid'] or not safe['cid'] or not safe['title'].strip():raise ContractError('缺少视频标识/标题')
        for key,limit in (('desc',100000),('tname',1000),('pic',4000)):
            if key in data:safe[key]=bounded_text(data[key],limit)
        for key in ('duration','pubdate'):
            if key in data:safe[key]=bounded_int(data[key],2**40)
        if 'owner' in data:
            owner=data['owner']
            if not isinstance(owner,dict):raise ContractError('作者格式无效')
            safe['owner']={'mid':bounded_int(owner.get('mid'),2**63-1),'name':bounded_text(owner.get('name'),2000)}
            if not safe['owner']['mid']:raise ContractError('作者标识无效')
        if 'stat' in data:
            if not isinstance(data['stat'],dict):raise ContractError('统计格式无效')
            safe['stat']={key:bounded_int(value,10**15) for key,value in data['stat'].items() if key in ('view','danmaku','reply','favorite','coin','like','share','dislike','now_rank','his_rank')}
        if 'pages' in data:
            if not isinstance(data['pages'],list) or len(data['pages'])>1000:raise ContractError('分P列表无效')
            pages=[]
            for page in data['pages']:
                if not isinstance(page,dict):raise ContractError('分P无效')
                item={'cid':bounded_int(page.get('cid'),2**63-1)}
                if not item['cid']:raise ContractError('分P标识无效')
                if 'part' in page:item['part']=bounded_text(page['part'],2000)
                for key in ('page','duration'):
                    if key in page:item[key]=bounded_int(page[key],2**40)
                pages.append(item)
            if pages and pages[0]['cid']!=safe['cid']:raise ContractError('首P与视频CID不一致')
            safe['pages']=pages
        return safe
    except (ContractError,ValueError,TypeError):raise Fault('视频元数据类型、范围或身份校验失败') from None

def subtitle_segment(segment):
    try:
        if not isinstance(segment,dict):raise ContractError('字幕段无效')
        start,end=segment.get('from'),segment.get('to')
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (start,end)) or not 0<=start<=end<=31536000:raise ContractError('字幕时间无效')
        return {'from':start,'to':end,'content':bounded_text(segment.get('content'),30000)}
    except (ContractError,ValueError,TypeError):raise Fault('字幕段文本或时间校验失败') from None
