"""Owner-enabled bounded public-feed queue; no outbound actions."""
from datetime import datetime, timezone
import json
import threading
import time
from .runtime import Fault,canonical,digest
from .bili import BV

class BrowseQueue:
    def __init__(self,workbench):
        self.w=workbench;self.stop_event=threading.Event();self.thread=None;db=self.w.s.db
        db.executescript('''CREATE TABLE IF NOT EXISTS browse_control(id INTEGER PRIMARY KEY,enabled INTEGER,interval_s INTEGER,next_at REAL,reason TEXT);
        INSERT OR IGNORE INTO browse_control VALUES(1,0,300,0,'未启动');
        CREATE TABLE IF NOT EXISTS browse_jobs(key TEXT PRIMARY KEY,account TEXT,bvid TEXT,state TEXT,updated REAL,result TEXT);
        CREATE TABLE IF NOT EXISTS browse_attempts(id INTEGER PRIMARY KEY,job_key TEXT,created REAL);''')
        db.execute('INSERT INTO browse_attempts(job_key,created) SELECT key,updated FROM browse_jobs WHERE NOT EXISTS(SELECT 1 FROM browse_attempts)')
        db.execute("UPDATE browse_jobs SET state='uncertain' WHERE state='running'")
        db.execute("UPDATE browse_control SET enabled=0,reason='启动后需主人明确恢复' WHERE id=1")
    def state(self):
        c=dict(self.w.s.db.execute('SELECT * FROM browse_control WHERE id=1').fetchone())
        c['jobs']=[dict(x) for x in self.w.s.db.execute('SELECT key,bvid,state,updated FROM browse_jobs ORDER BY updated DESC LIMIT 100')]
        c['worker_alive']=bool(self.thread and self.thread.is_alive())
        c['source']='公开热门前10条中每轮最多处理3条，不是个性化推荐流';return c
    def key(self,c,bvid):
        fields=('provider_url','model','vision_model','price_version','vision_price_version','input_price_micro_per_million','output_price_micro_per_million','vision_input_price_micro_per_million','vision_output_price_micro_per_million','max_input_tokens','max_output_tokens','currency','browse_visual_fallback','paid_enabled')
        return digest([self.w.bili.identity_key(),bvid,{k:c[k] for k in fields},'subtitle-visual-route-v2'])
    def enable(self,interval=300):
        if type(interval) is not int or not 60<=interval<=86400:raise Fault('间隔须为60至86400秒')
        c=self.w.s.config()
        if not c['read_enabled']:raise Fault('未授权B站读取')
        if not c['account_mid']:raise Fault('须先明确独立账号MID，队列绑定账号')
        if self.w.s.db.execute("SELECT 1 FROM charges WHERE state IN ('reserved','uncertain')").fetchone():raise Fault('有不确定费用，不能启动队列')
        self.w.cancel_event.clear()
        try:self.w.bili.ensure_read_identity()
        except Exception:
            self.w.cancel_event.set();raise
        self.w.s.db.execute("UPDATE browse_control SET enabled=1,interval_s=?,next_at=0,reason='' WHERE id=1",(interval,))
        if self.thread is None:
            self.thread=threading.Thread(target=self._loop,daemon=True);self.thread.start()
        return self.state()
    def pause(self,reason='主人暂停'):
        self.w.cancel_event.set()
        with self.w.s.lock:self.w.s.db.execute('UPDATE browse_control SET enabled=0,reason=? WHERE id=1',(reason,))
        return self.state()
    def retry_job(self,key,*,upgrade_metadata=False,confirmation=''):
        if not isinstance(key,str) or len(key)!=64 or any(x not in '0123456789abcdef' for x in key):raise Fault('任务键无效')
        if type(upgrade_metadata) is not bool:raise Fault('重排类型无效')
        with self.w.s.lock:
            row=self.w.s.db.execute('SELECT * FROM browse_jobs WHERE key=?',(key,)).fetchone()
            if not row:raise Fault('任务不存在')
            if row['state']=='done':
                if not upgrade_metadata or confirmation!='确认重新分析元数据':raise Fault('重新分析元数据需要明确确认')
                try:old_result=json.loads(row['result'])
                except (TypeError,ValueError):raise Fault('原任务结果不可核验，不予重排') from None
                if not isinstance(old_result,dict) or old_result.get('coverage')!='仅推荐元数据' or old_result.get('model_called') is not False:
                    raise Fault('只允许重新分析未调用模型的元数据任务')
                if not self.w.s.config()['paid_enabled']:raise Fault('尚未启用模型分析，不予重排')
            elif row['state'] not in ('blocked','needs_input') or upgrade_metadata:
                raise Fault('只可明确重试未执行模型的阻塞任务')
            if self.w.s.db.execute('SELECT 1 FROM charges WHERE id LIKE ?',('model:queue:'+key+'%',)).fetchone():raise Fault('已有用量或不确定费用，禁止重放')
            if row['account']!=self.w.s.config()['account_mid']:raise Fault('账号已改变，不迁移旧域任务')
            current_key=self.key(self.w.s.config(),row['bvid'])
            if row['state']=='done' and current_key==key:raise Fault('分析配置未变化，不予重排')
            if current_key!=key and self.w.s.db.execute('SELECT 1 FROM charges WHERE id LIKE ?',('model:queue:'+current_key+'%',)).fetchone():raise Fault('新方案已有费用记录，禁止重放')
            self.w.s.db.execute('BEGIN IMMEDIATE')
            try:
                if current_key!=key:
                    if self.w.s.db.execute('SELECT 1 FROM browse_jobs WHERE key=?',(current_key,)).fetchone():raise Fault('新执行方案已有任务，请先审查其状态')
                    self.w.s.db.execute("INSERT INTO browse_jobs VALUES (?,?,?,'queued',?,NULL)",(current_key,row['account'],row['bvid'],time.time()))
                    self.w.s.db.execute("UPDATE browse_jobs SET state='superseded' WHERE key=?",(key,));key=current_key
                else:self.w.s.db.execute("UPDATE browse_jobs SET state='queued' WHERE key=?",(key,))
                self.w.s.db.execute("UPDATE browse_control SET enabled=0,reason='任务已排队，需主人明确恢复' WHERE id=1")
                self.w.s.db.commit()
            except Exception:
                self.w.s.db.rollback();raise
            return {'queued':True,'key':key,'requires_explicit_resume':True,'automatic_outbound':False}
    def _wait_off_peak(self,detail):
        from datetime import datetime
        at=datetime.fromisoformat(detail['resume_at_utc']).timestamp()
        self.w.s.db.execute("UPDATE browse_control SET next_at=?,reason='DeepSeek等待低谷；未发模型请求' WHERE id=1",(max(time.time()+30,at),))
    def tick(self):
        with self.w.s.lock:
            row=self.w.s.db.execute('SELECT * FROM browse_control WHERE id=1').fetchone()
            if row['enabled'] and self.w.cancel_event.is_set():
                self.w.s.db.execute("UPDATE browse_control SET enabled=0,reason='主人已取消；需明确重新开启' WHERE id=1")
                return
            if not row['enabled'] or row['next_at']>time.time():return
            self.w.s.db.execute('UPDATE browse_control SET next_at=? WHERE id=1',(time.time()+row['interval_s'],))
            try:
                c=self.w.s.config()
                from .deepseek_test import require_window,OffPeakDeferred
                if c['paid_enabled']:
                    try:require_window(c['provider_url'])
                    except OffPeakDeferred as wait:
                        self._wait_off_peak(wait.detail);return
                self.w.bili.ensure_read_identity()
                since=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
                count=self.w.s.db.execute('SELECT count(*) FROM browse_attempts WHERE created>=?',(since,)).fetchone()[0]
                if count>=c['browse_daily_limit']:raise Fault('今日浏览尝试次数已到上限')
                queued=self.w.s.db.execute("SELECT bvid FROM browse_jobs WHERE state IN ('queued','deferred') AND account=? LIMIT 3",(c['account_mid'],)).fetchall()
                if queued:items=[dict(x) for x in queued]
                else:
                    candidates=self.w.bili.feed().get('list',[])[:10]
                    seen=[r['bvid'] for r in self.w.s.db.execute('SELECT bvid FROM browse_jobs WHERE account=?',(c['account_mid'],)) if self.w.s.db.execute('SELECT 1 FROM browse_jobs WHERE key=?',(self.key(c,r['bvid']),)).fetchone()]
                    choice=self.w.interests.select(candidates,max_items=3,seen=seen)
                    items=choice['items']
                if not isinstance(items,list):raise Fault('热门列表格式无效')
                processed=0
                for item in items:
                    if self.w.cancel_event.is_set():break
                    if not isinstance(item,dict):raise Fault('热门条目格式无效')
                    bvid=item.get('bvid','')
                    if not isinstance(bvid,str) or not BV.fullmatch(bvid):continue
                    key=self.key(c,bvid);old=self.w.s.db.execute('SELECT state FROM browse_jobs WHERE key=?',(key,)).fetchone()
                    if old and old['state'] not in ('queued','deferred'):continue
                    if count>=c['browse_daily_limit'] or processed>=3:break
                    if old:self.w.s.db.execute("UPDATE browse_jobs SET state='running' WHERE key=?",(key,))
                    else:self.w.s.db.execute("INSERT INTO browse_jobs VALUES (?,?,?,'running',?,NULL)",(key,c['account_mid'],bvid,time.time()))
                    self.w.s.db.execute('INSERT INTO browse_attempts(job_key,created) VALUES (?,?)',(key,time.time()));count+=1;processed+=1
                    try:
                        result=self.w.analyze(bvid,'queue:'+key,mode='auto' if c['browse_visual_fallback'] else 'subtitle',refresh_subtitles=bool(old)) if c['paid_enabled'] else {'bvid':bvid,'title':item.get('title'),'coverage':'仅推荐元数据','model_called':False}
                        if result.get('status')=='deferred_for_off_peak':
                            self.w.s.db.execute("UPDATE browse_jobs SET state='deferred',result=? WHERE key=?",(canonical(result),key));self._wait_off_peak(result['plan']['time_gate']);break
                        needs_input=result.get('status') in ('needs_frames','needs_model_configuration','needs_dependencies','needs_video_confirmation')
                        self.w.s.db.execute('UPDATE browse_jobs SET state=?,updated=?,result=? WHERE key=?',('needs_input' if needs_input else 'done',time.time(),canonical(result),key))
                        coverage='unavailable' if needs_input else ('metadata' if not c['paid_enabled'] else (('sparse_frames' if result.get('result',{}).get('coverage','').startswith('仅稀疏') else 'subtitle_and_frames') if result.get('result',{}).get('sampling') else 'subtitle'))
                        if result.get('status')=='needs_video_confirmation':
                            prior=self.w.s.db.execute('SELECT 1 FROM public_video_observations WHERE account=? AND bvid=?',(c['account_mid'] or 'anonymous',bvid)).fetchone()
                            if not prior:self.w.interests.record_observation(bvid,'metadata','公开B站队列资料，非主人偏好')
                        else:self.w.interests.record_observation(bvid,coverage,'公开B站队列资料，非主人偏好',result.get('material_id',''))
                        if needs_input:
                            reason='视频分析需要逐项准备并确认；队列已暂停' if result.get('status')=='needs_video_confirmation' else '缺少字幕/模型预算/视觉依赖，未理解完成；需主人检查'
                            self.w.s.db.execute('UPDATE browse_control SET enabled=0,reason=? WHERE id=1',(reason,));break
                    except OffPeakDeferred as wait:
                        self.w.s.db.execute("UPDATE browse_jobs SET state='deferred' WHERE key=?",(key,));self._wait_off_peak(wait.detail);break
                    except Exception:
                        consumed=self.w.s.db.execute('SELECT 1 FROM charges WHERE id LIKE ?',('model:queue:'+key+'%',)).fetchone()
                        self.w.s.db.execute('UPDATE browse_jobs SET state=?,updated=? WHERE key=?',('uncertain' if consumed else 'blocked',time.time(),key));raise
            except Exception as e:
                reason=str(e) if isinstance(e,Fault) else '队列失败，需人工核对'
                self.w.s.db.execute('UPDATE browse_control SET enabled=0,reason=? WHERE id=1',(reason,))
    def _loop(self):
        while not self.stop_event.wait(1):self.tick()
    def close(self):
        self.stop_event.set();self.w.cancel_event.set()
        if self.thread is not None:self.thread.join(timeout=35)
