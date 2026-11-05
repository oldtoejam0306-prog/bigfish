"""Two-step, local-only allowlisted application launches with an audit trail."""
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from .runtime import Fault


class ComputerActions:
    ALLOWED={'notepad':('记事本','notepad.exe'),'calculator':('计算器','calc.exe')}
    def __init__(self,state,launcher=None,path_resolver=None):
        self.s=state;self.launcher=launcher or self._launch;self.path_resolver=path_resolver or self._path
        self.s.db.execute('CREATE TABLE IF NOT EXISTS computer_action_drafts (id TEXT PRIMARY KEY,action TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)')
        self.s.db.execute("UPDATE computer_action_drafts SET state='uncertain' WHERE state='running'")

    @staticmethod
    def _path(filename):
        if os.name!='nt':raise Fault('此操作只支持 Windows')
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        length=ctypes.windll.kernel32.GetSystemDirectoryW(buffer,len(buffer))
        if not 0<length<len(buffer):raise Fault('无法确定 Windows 系统目录')
        path=Path(buffer.value)/filename
        if not path.is_file():raise Fault('Windows 自带应用不可用')
        return path

    @staticmethod
    def _launch(path):
        # The selected app is explicitly interactive and remains visible.
        subprocess.Popen([str(path)],shell=False,close_fds=True)

    def prepare(self,action,now=None):
        if not isinstance(action,str) or action not in self.ALLOWED:raise Fault('操作不在允许列表中')
        now=time.time() if now is None else now
        with self.s.lock:
            draft=secrets.token_hex(12)
            self.s.db.execute("INSERT INTO computer_action_drafts VALUES (?,?,'pending',?,?)",(draft,action,now,now))
        return {'id':draft,'description':'打开 Windows 自带的'+self.ALLOWED[action][0],'confirmation':'确认打开'+self.ALLOWED[action][0],'expires_in_seconds':60,'executed':False}

    def execute(self,draft,confirmation,now=None):
        if not isinstance(draft,str) or not re.fullmatch(r'[0-9a-f]{24}',draft):raise Fault('操作编号无效')
        now=time.time() if now is None else now
        with self.s.lock:
            row=self.s.db.execute('SELECT * FROM computer_action_drafts WHERE id=?',(draft,)).fetchone()
            if not row or row['state']!='pending':raise Fault('操作不存在、已执行或结果不确定，不能重试')
            if now<row['created'] or now-row['created']>60:
                self.s.db.execute("UPDATE computer_action_drafts SET state='expired',updated=? WHERE id=?",(now,draft));raise Fault('确认已过期，请重新准备')
            label,filename=self.ALLOWED[row['action']]
            if confirmation!='确认打开'+label:raise Fault('需要逐次确认此操作')
            path=self.path_resolver(filename)
            self.s.db.execute("UPDATE computer_action_drafts SET state='running',updated=? WHERE id=?",(now,draft))
        try:self.launcher(path)
        except Exception:
            with self.s.lock:self.s.db.execute("UPDATE computer_action_drafts SET state='uncertain',updated=? WHERE id=?",(time.time(),draft))
            raise Fault('操作未能确认是否完成，请先检查桌面，不会自动重试') from None
        with self.s.lock:self.s.db.execute("UPDATE computer_action_drafts SET state='launched',updated=? WHERE id=?",(time.time(),draft))
        return {'state':'launched','description':'已请求打开'+label,'automatic_retry':False}

    def audit(self):
        with self.s.lock:rows=self.s.db.execute('SELECT action,state,created,updated FROM computer_action_drafts ORDER BY created DESC LIMIT 30').fetchall()
        return {'items':[dict(row) for row in rows]}
