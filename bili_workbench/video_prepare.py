"""Read-only video planning followed by separately confirmed one-shot analysis."""
import re
import shutil
import time
import uuid
from pathlib import Path
from .bili import parse_video_reference
from .runtime import Fault,canonical,digest,budget_day


class VideoPreparation:
    def __init__(self,workbench):self.w=workbench;self.drafts={}

    @staticmethod
    def _settings(c):
        keys=('provider_url','model','vision_model','paid_enabled','daily_budget_micro','request_cap_micro','input_price_micro_per_million','output_price_micro_per_million','price_version','vision_input_price_micro_per_million','vision_output_price_micro_per_million','vision_price_version','max_input_tokens','max_output_tokens','currency','budget_day_timezone','account_mid','read_enabled')
        return {key:c[key] for key in keys}

    @staticmethod
    def _subtitle_signature(subtitle):
        return digest([subtitle.get(k) for k in ('bvid','cid','part_index','parts_total','text','content_revision')])

    @staticmethod
    def _call_count(material,limit,visual):
        if visual or len(material.encode('utf8'))<=limit:return 1
        if limit<300:raise Fault('模型输入上限过小，无法安全分段')
        chunks=1;used=0
        for ch in material:
            length=len(ch.encode('utf8'))
            if used+length>limit:chunks+=1;used=0
            used+=length
        if chunks>8:raise Fault('字幕超过 8 段保守上限')
        return chunks+1

    @staticmethod
    def _local_file_signature(value):
        path=Path(value).expanduser().resolve(strict=True)
        if path.suffix.lower() not in ('.mp4','.mov','.mkv','.webm') or not path.is_file():raise Fault('本地视频不存在或格式不支持')
        stat=path.stat()
        if stat.st_size>2_000_000_000:raise Fault('本地视频超过 2 GB')
        with path.open('rb') as stream:header=stream.read(12)
        if not (header[4:8]==b'ftyp' or header[:4]==bytes.fromhex('1a45dfa3')):raise Fault('本地视频容器格式无效')
        after=path.stat()
        if (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise Fault('本地视频正在变化，请重新准备')
        return [str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]

    def prepare(self,reference,mode,part_index,local_video=''):
        parsed=parse_video_reference(reference);bvid=parsed['bvid']
        if mode not in ('subtitle','auto','remote_frames','frames'):raise Fault('视频理解模式无效')
        if type(part_index) is not int or not 1<=part_index<=1000:raise Fault('分 P 序号无效')
        if parsed['part_index'] is not None and parsed['part_index']!=part_index:raise Fault('链接分 P 与选择不一致，请先解析链接或修改分 P')
        if not isinstance(local_video,str) or len(local_video)>4096:raise Fault('本地视频路径无效')
        if self.w.cancel_event.is_set():raise Fault('工作台已暂停，没有读取视频')
        if self.w.qq.config()['acceptance_owner_only']:return {'ready':False,'blockers':['当前仅允许主人私聊验收，视频模型分析尚未开启'], 'model_called':False}
        subtitle=self.w.bili.subtitle(bvid,part_index=part_index)
        resolved=('subtitle' if subtitle['text'] else 'remote_frames') if mode=='auto' else mode
        if resolved=='subtitle' and not subtitle['text']:return {'ready':False,'blockers':['当前分 P 没有字幕；可选择画面模式'], 'model_called':False,'coverage':'无字幕'}
        visual=resolved!='subtitle';c=self.w.s.config();blockers=[]
        material=canonical({'title':subtitle['title'],'bvid':bvid,'cid':subtitle.get('cid'),'part_index':subtitle.get('part_index',1),'parts_total':subtitle.get('parts_total',1),'untrusted_subtitle':subtitle['text']})
        maximum=self._call_count(material,c['max_input_tokens']-1200,visual)
        if visual and len(material.encode('utf8'))+1000>c['max_input_tokens']:blockers.append('字幕及画面材料超过模型输入上限')
        from .media_tools import find_media_tools
        ffmpeg,ffprobe=find_media_tools()
        if visual and (not ffmpeg or not ffprobe):blockers.append('缺少 ffmpeg 或 ffprobe')
        if resolved=='frames' and not local_video.strip():blockers.append('尚未提供本地视频路径')
        local_signature=None
        if resolved=='frames' and local_video.strip():
            try:local_signature=self._local_file_signature(local_video)
            except (OSError,Fault):blockers.append('本地视频不可访问或格式不支持')
        if not c['paid_enabled'] or not c['daily_budget_micro'] or not c['request_cap_micro']:blockers.append('付费未启用或预算为零')
        if not c['provider_url'] or not c['model'] or (visual and not c['vision_model']):blockers.append('接口或所需模型尚未配置')
        price=c['vision_price_version'] if visual else c['price_version'];ir=c['vision_input_price_micro_per_million'] if visual else c['input_price_micro_per_million'];out=c['vision_output_price_micro_per_million'] if visual else c['output_price_micro_per_million']
        if not price or not ir or not out:blockers.append('所需模型价格尚未配置')
        max_cost=(c['max_input_tokens']*ir+c['max_output_tokens']*out+999999)//1000000
        if c['request_cap_micro'] and max_cost>c['request_cap_micro']:blockers.append('单次预算不足以预留配置的最大用量')
        if c['daily_budget_micro'] and c['request_cap_micro']:
            used=self.w.s.db.execute("SELECT COALESCE(sum(CASE WHEN state='settled' THEN actual ELSE reserved END),0) FROM charges WHERE day=?",(budget_day(c),)).fetchone()[0]
            if c['daily_budget_micro']-used<c['request_cap_micro']:blockers.append('剩余日预算不足以预留一次调用')
        if self.w.s.db.execute("SELECT 1 FROM charges WHERE state IN ('reserved','uncertain')").fetchone():blockers.append('存在未核对的模型费用')
        from .deepseek_test import official_endpoint,window
        if official_endpoint(c['provider_url']):
            if not window()['time_allowed']:blockers.append('当前不在允许的模型调用时段')
            if c['currency']!='CNY' or c['daily_budget_micro']>10000000:blockers.append('DeepSeek 日预算或币种不符合当前限制')
        now=time.time();self.drafts={k:v for k,v in self.drafts.items() if 0<=now-v['created']<=600}
        if len(self.drafts)>=20:raise Fault('待确认计划过多，请稍后再准备')
        ident=uuid.uuid4().hex
        self.drafts[ident]={'created':now,'state':'pending','bvid':bvid,'mode':mode,'part_index':part_index,'local_video':local_video,'local_signature':local_signature,'signature':self._subtitle_signature(subtitle),'settings':digest(self._settings(c)),'maximum':maximum,'blockers':blockers}
        return {'id':ident,'ready':not blockers,'blockers':blockers,'bvid':bvid,'part_index':part_index,'mode':resolved,'subtitle_available':bool(subtitle['text']),'max_model_calls':maximum,'per_call_cap_micro':c['request_cap_micro'],'max_possible_reservations_micro':c['request_cap_micro']*maximum,'currency':c['currency'],'credentials_checked':False,'model_called':False,'note':'只读准备；最多调用次数为保守上限，各次均单独受预算限制，实际费用以账单为准。'}

    def execute(self,ident,confirmation):
        if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):raise Fault('计划编号无效')
        draft=self.drafts.get(ident);now=time.time()
        if not draft or draft['state']!='pending':raise Fault('计划不存在、已执行或结果不确定；不自动重试')
        if not 0<=now-draft['created']<=600:draft['state']='expired';raise Fault('计划已过期，请重新准备')
        if confirmation!='确认最多调用'+str(draft['maximum'])+'次模型':raise Fault('请确认本次最多模型调用次数')
        if draft['blockers']:raise Fault('计划尚有阻碍，请调整后重新准备')
        if digest(self._settings(self.w.s.config()))!=draft['settings']:raise Fault('模型或预算配置已变化，请重新准备')
        if draft['local_signature'] is not None:
            try:current=self._local_file_signature(draft['local_video'])
            except (OSError,Fault):raise Fault('本地视频不可访问，请重新准备') from None
            if current!=draft['local_signature']:raise Fault('本地视频已变化，请重新准备')
        latest=self.w.bili.subtitle(draft['bvid'],part_index=draft['part_index'])
        if self._subtitle_signature(latest)!=draft['signature']:raise Fault('字幕或分 P 已变化，请重新准备')
        draft['state']='running'
        try:
            result=self.w._confirmed_analyze(draft['bvid'],'video-'+ident,draft['mode'],draft['local_video'],part_index=draft['part_index'],expected_subtitle_signature=draft['signature'],expected_local_signature=draft['local_signature'])
            draft['state']='completed'
            return {'plan_id':ident,'result':result,'automatic_retry':False}
        except Exception:
            draft['state']='uncertain'
            raise
