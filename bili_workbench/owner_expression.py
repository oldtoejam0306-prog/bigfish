"""Owner-only, rule-based expression choices. No model calls or networking."""
import base64,io,json,time,wave
from datetime import datetime,timezone
from bigfish_persona import night_key,is_sleep_nudge
from .runtime import Fault,canonical

DEFAULT={'stickers':True,'emotional_voice':True,'qq_media_enabled':False,'qq_media_validated':False,'native_voice_authorized':False}

def mood(user,reply):
    text=user+'\n'+reply
    if any(s in text for s in ('没过','没被录取','不开心','难过','委屈','失败','伤心')):return 'comfort',any(s in text for s in ('委屈','难过','伤心'))
    if any(s in text for s in ('面试过了','考过了','录取了','拿到offer','生日','太开心','好耶','太棒了')):return 'happy',True
    if any(s in text for s in ('真棒','喜欢你','可爱','漂亮','谢谢','开心','夸你')):return 'happy',False
    if any(s in text for s in ('摸鱼','大肥鱼','才没有','笨蛋','嫌弃','得意')):return 'teasing',False
    return 'neutral',False

def validate_binary(kind,encoded):
    if not isinstance(encoded,str) or len(encoded)>2_000_000:raise Fault('媒体内容无效或过大')
    try:raw=base64.b64decode(encoded,validate=True)
    except Exception:raise Fault('媒体编码无效') from None
    if kind=='image':
        from .stickers import Stickers
        Stickers.validate_png(raw)
    elif kind=='record':
        if not raw.startswith(b'RIFF') or len(raw)>1_500_000:raise Fault('语音内容无效或过大')
        try:
            with wave.open(io.BytesIO(raw),'rb') as sound:
                if sound.getnchannels()!=1 or sound.getsampwidth()!=2 or sound.getframerate()!=16000 or not 0<sound.getnframes()<=320000:raise Fault('语音须为20秒内16kHz单声道PCM')
                if len(sound.readframes(sound.getnframes()))!=sound.getnframes()*2:raise Fault('语音数据不完整')
        except (wave.Error,EOFError):raise Fault('语音格式无效') from None
    else:raise Fault('媒体类型不支持')
    return raw

class OwnerExpression:
    def __init__(self,w,clock=None):
        self.w=w;self.clock=clock or (lambda:datetime.now(timezone.utc))
        w.s.db.executescript('''CREATE TABLE IF NOT EXISTS owner_expression_settings(id INTEGER PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS owner_expression_counts(channel TEXT PRIMARY KEY,total INTEGER NOT NULL,last_voice INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS owner_expression_events(id TEXT PRIMARY KEY,channel TEXT NOT NULL,position INTEGER NOT NULL,mood TEXT NOT NULL,sticker_id TEXT,sticker_name TEXT,voice_due INTEGER NOT NULL,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_reply_expression(turn_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,character_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS owner_sleep_nudges(night TEXT PRIMARY KEY,event_id TEXT NOT NULL);''')
        w.s.db.execute('INSERT OR IGNORE INTO owner_expression_settings VALUES (1,?)',(canonical(DEFAULT),))
    def settings(self):return dict(DEFAULT,**json.loads(self.w.s.db.execute('SELECT value FROM owner_expression_settings WHERE id=1').fetchone()[0]))
    def configure(self,values):
        if not isinstance(values,dict) or set(values)-set(DEFAULT) or any(type(v) is not bool for v in values.values()):raise Fault('表达选项无效')
        with self.w.s.lock:
            value=dict(self.settings(),**values);self.w.s.db.execute('UPDATE owner_expression_settings SET value=? WHERE id=1',(canonical(value),));return value
    def night_allowed(self):
        key=night_key(self.clock())
        return bool(key and not self.w.s.db.execute('SELECT 1 FROM owner_sleep_nudges WHERE night=?',(key,)).fetchone())
    def filter_sleep(self,text):
        if night_key(self.clock()) is None or self.night_allowed():return text
        lines=[line for line in text.splitlines() if not is_sleep_nudge(line)]
        return '\n'.join(lines) or '在呢'
    def record_sleep(self,event_id,text):
        key=night_key(self.clock())
        if key and is_sleep_nudge(text):self.w.s.db.execute('INSERT OR IGNORE INTO owner_sleep_nudges VALUES (?,?)',(key,event_id))
    def plan(self,channel,event_id,user,reply,*,owner_private):
        if channel not in ('local','qq') or not isinstance(event_id,str) or not 1<=len(event_id)<=160:raise Fault('表达来源无效')
        if not owner_private:return {'eligible':False,'sticker':None,'voice_due':False,'mood':'neutral','rule_based':True}
        with self.w.s.lock:
            old=self.w.s.db.execute('SELECT 1 FROM owner_expression_events WHERE id=?',(event_id,)).fetchone()
            if old:return self.metadata(event_id)
            self.w.s.db.execute('INSERT OR IGNORE INTO owner_expression_counts VALUES (?,0,-3)',(channel,));counter=self.w.s.db.execute('SELECT * FROM owner_expression_counts WHERE channel=?',(channel,)).fetchone();position=counter['total']+1
            tone,strong=mood(user,reply);settings=self.settings();chosen=None
            words={'happy':('开心','笑','得意','庆祝','好耶'),'comfort':('抱抱','安慰','委屈','摸头'),'teasing':('嫌弃','哼','摸鱼','得意')}.get(tone,())
            last_sticker=self.w.s.db.execute("SELECT COALESCE(MAX(position),0) FROM owner_expression_events WHERE channel=? AND state='committed' AND sticker_id IS NOT NULL",(channel,)).fetchone()[0]
            if settings['stickers'] and position-last_sticker>=4 and words:
                chosen=next((item for item in self.w.stickers.list()['items'] if any(word in item['name'] for word in words)),None)
            voice=bool(settings['emotional_voice'] and strong and position-counter['last_voice']>=3 and len(reply)<=150)
            self.w.s.db.execute("INSERT INTO owner_expression_events VALUES (?,?,?,?,?,?,?,'planned')",(event_id,channel,position,tone,chosen['id'] if chosen else None,chosen['name'] if chosen else None,int(voice)))
            return self.metadata(event_id)
    def metadata(self,event_id):
        row=self.w.s.db.execute('SELECT * FROM owner_expression_events WHERE id=?',(event_id,)).fetchone()
        if not row:return {'eligible':False,'sticker':None,'voice_due':False,'mood':'neutral','rule_based':True}
        available=bool(row['sticker_id'] and self.w.s.db.execute('SELECT 1 FROM local_stickers WHERE id=?',(row['sticker_id'],)).fetchone())
        return {'eligible':True,'sticker':{'id':row['sticker_id'],'name':row['sticker_name'],'available':available} if row['sticker_id'] else None,'voice_due':bool(row['voice_due']),'mood':row['mood'],'rule_based':True}
    def commit(self,event_id,text):
        with self.w.s.lock:
            row=self.w.s.db.execute('SELECT * FROM owner_expression_events WHERE id=?',(event_id,)).fetchone()
            if not row or row['state']=='committed':return
            self.w.s.db.execute('UPDATE owner_expression_counts SET total=total+1,last_voice=CASE WHEN ? THEN total+1 ELSE last_voice END WHERE channel=?',(row['voice_due'],row['channel']));self.w.s.db.execute("UPDATE owner_expression_events SET state='committed' WHERE id=?",(event_id,));self.record_sleep(event_id,text)
    def segments(self,event_id,text,*,voice_renderer=None):
        plan=self.metadata(event_id);segments=[{'type':'text','data':{'text':text}}];status={'image_prepared':False,'voice_prepared':False,'voice_due':plan['voice_due'],'sent':False}
        sticker=plan['sticker']
        if sticker and sticker['available']:
            try:
                image=self.w.stickers.get(sticker['id'])['png_base64'];validate_binary('image',image);segments.append({'type':'image','data':{'file':'base64://'+image}});status['image_prepared']=True
            except Fault:pass
        if plan['voice_due'] and voice_renderer is not None and self.settings()['native_voice_authorized']:
            try:
                audio=voice_renderer(text)
                with wave.open(io.BytesIO(audio),'rb') as original:
                    if original.getframerate()<=0 or original.getnframes()>original.getframerate()*20:raise Fault('语音超过20秒，保留文字')
                # Reuse the existing PCM converter; native system voices may
                # return another sample rate or stereo. No external converter.
                from .duplex_adapters import _wav_to_pcm16_16k
                pcm=_wav_to_pcm16_16k(audio);buffer=io.BytesIO()
                with wave.open(buffer,'wb') as normalized:normalized.setnchannels(1);normalized.setsampwidth(2);normalized.setframerate(16000);normalized.writeframes(pcm)
                encoded=base64.b64encode(buffer.getvalue()).decode('ascii');validate_binary('record',encoded);segments.append({'type':'record','data':{'file':'base64://'+encoded}});status['voice_prepared']=True
            except Exception:status['voice_unavailable']=True
        return segments,status
