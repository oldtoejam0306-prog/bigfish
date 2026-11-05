"""Optional low-rate reaction worker, no account discovery or unsolicited sends.
All flags default off; acceptance profile never automatically sends messages.
"""
import threading
from .runtime import Fault,canonical
from .deepseek_test import require_window,OffPeakDeferred

class InteractionWorker:
    def __init__(self,w):
        self.w=w;self.stop_event=threading.Event();self.thread=None
        w.s.db.execute('CREATE TABLE IF NOT EXISTS interaction_status(id INTEGER PRIMARY KEY,value TEXT)')
        w.s.db.execute('INSERT OR IGNORE INTO interaction_status VALUES (1,?)',(canonical({'state':'off','sent':0}),))
    def state(self):
        import json
        return dict(json.loads(self.w.s.db.execute('SELECT value FROM interaction_status WHERE id=1').fetchone()[0]),worker_alive=bool(self.thread and self.thread.is_alive()))
    def _state(self,**values):self.w.s.db.execute('UPDATE interaction_status SET value=? WHERE id=1',(canonical(values),))
    def start_if_enabled(self):
        if not self.w.s.config()['qq_auto_process']:return self.state()
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear();self.thread=threading.Thread(target=self._loop,daemon=True);self.thread.start()
        return self.state()
    def pause(self):
        self.w.cancel_event.set();self.w.s.configure({'qq_auto_process':False,'qq_auto_send':False});self._state(state='paused',sent=0);return self.state()
    def tick(self):
        with self.w.s.lock:
            c=self.w.s.config()
            if not c['qq_auto_process'] or self.stop_event.is_set():return
            if not c['qq_transport_validated'] or not self.w.qq.config()['enabled']:
                self._state(state='needs_transport_validation',sent=0);return
            if self.w.cancel_event.is_set():self._state(state='cancelled',sent=0);return
            try:
                require_window(c['provider_url'])
                deferred=self.w.resume_qq_deferred(1,authenticated_only=True)
                values=deferred.get('results',[])
                if not values:values=self.w.process_qq_inbox(1).get('results',[])
                sent=0
                for result in values:
                    if self.stop_event.is_set() or self.w.cancel_event.is_set():break
                    if result.get('status') not in ('model_draft',):continue
                    current=self.w.s.config()
                    if not current['qq_auto_send'] or not current['qq_sender_enabled'] or self.w.qq.config()['acceptance_owner_only']:continue
                    if result.get('transport_authenticated') is not True:continue
                    draft=result.get('transport_draft')
                    if not draft:draft=self.w.prepare_qq_reply(result['event_key'],'确认准备QQ回复')
                    self.w.send_qq(draft['id'],'确认发送');sent+=1
                self._state(state='processed' if values else 'idle',sent=sent,automatic_publish=False)
            except OffPeakDeferred as wait:
                self._state(state='waiting_off_peak',resume_at_utc=wait.detail['resume_at_utc'],sent=0)
            except Exception:
                # Do not repeat unknown sends or keep spending on undelivered replies.
                self.w.s.configure({'qq_auto_process':False,'qq_auto_send':False})
                self._state(state='paused_needs_review',sent=0,note='错误正文不回显；未自动重试未知发送/费用')
    def _loop(self):
        while not self.stop_event.wait(5):self.tick()
    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=35)
