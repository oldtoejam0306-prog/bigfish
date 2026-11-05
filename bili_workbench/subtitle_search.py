"""Session-only search over explicitly read subtitles; no network or model."""
import math
from .bili import parse_video_reference
from .runtime import Fault


class SubtitleSearch:
    def __init__(self,state):self.s=state;self._current=None

    def load(self,bvid,part_index,subtitle):
        # Never trust a caller-provided account or retain prior-account material.
        if subtitle.get('bvid')!=bvid or subtitle.get('part_index',1)!=part_index:raise Fault('字幕与视频分 P 不匹配')
        segments=subtitle.get('segments')
        if not isinstance(segments,list) or len(segments)>20000:raise Fault('字幕段格式无效')
        from .validation import subtitle_segment
        clean=[subtitle_segment(x) for x in segments]
        if sum(len(x['content'].encode('utf8')) for x in clean)>1_000_000:raise Fault('字幕过长')
        with self.s.lock:self._current={'account':self.s.config()['account_mid'] or 'anonymous','bvid':bvid,'part_index':part_index,'segments':clean}

    def _selected(self,reference,part_index):
        parsed=parse_video_reference(reference);bvid=parsed['bvid']
        if type(part_index) is not int or not 1<=part_index<=1000:raise Fault('分 P 序号无效')
        if parsed['part_index'] is not None and parsed['part_index']!=part_index:raise Fault('链接分 P 与所选分 P 不一致')
        item=self._current
        if not item or item['account']!=(self.s.config()['account_mid'] or 'anonymous') or item['bvid']!=bvid or item['part_index']!=part_index:
            raise Fault('请先读取当前视频和分 P 的字幕')
        return item

    def search(self,reference,part_index,query):
        if not isinstance(query,str) or not query.strip() or len(query)>80 or any(ord(c)<32 for c in query):raise Fault('请输入 1 至 80 字的查找内容')
        query=query.strip().lower()
        with self.s.lock:
            item=self._selected(reference,part_index);bvid=item['bvid']
            matches=[];total=0
            for segment in item['segments']:
                content=segment['content'];position=content.lower().find(query)
                if position<0:continue
                total+=1
                if len(matches)<50:
                    begin=max(0,position-100);end=min(len(content),max(position+len(query)+100,begin+200))
                    matches.append({'from':segment['from'],'to':segment['to'],'text':('…' if begin else '')+content[begin:end]+('…' if end<len(content) else ''),'second':math.floor(segment['from']),'excerpt_truncated':bool(begin or end<len(content))})
        return {'bvid':bvid,'part_index':part_index,'matches':matches,'total':total,'truncated':total>len(matches),'coverage':'仅已读取字幕','network_called':False,'model_called':False}

    def page(self,reference,part_index,start=0):
        if type(start) is not int or not 0<=start<=20000:raise Fault('字幕页码无效')
        with self.s.lock:
            item=self._selected(reference,part_index);segments=item['segments'];rows=[]
            for segment in segments[start:start+20]:
                content=segment['content'];rows.append({'from':segment['from'],'to':segment['to'],'text':content[:1000]+('…' if len(content)>1000 else ''),'second':math.floor(segment['from']),'excerpt_truncated':len(content)>1000})
            next_index=start+len(rows) if start+len(rows)<len(segments) else None
            return {'bvid':item['bvid'],'part_index':part_index,'matches':rows,'total':len(segments),'next_index':next_index,'coverage':'仅已读取字幕','network_called':False,'model_called':False}
