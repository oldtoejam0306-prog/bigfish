"""Process-held workbench lock; never deletes uncertain legacy locks."""
import os
from pathlib import Path

_MARKER=b'BIGFISH_LOCK_V1\n'

class LockUnavailable(Exception):pass

def _legacy_pid_active(pid):
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
        except (PermissionError,OSError):return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];kernel.GetExitCodeProcess.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        return False if ctypes.get_last_error()==87 else None
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return None
        return code.value==259
    finally:kernel.CloseHandle(handle)

class RuntimeLock:
    def __init__(self,path):self.path=Path(path);self.fd=None
    def acquire(self):
        if self.fd is not None:raise LockUnavailable('该实例已经持有运行锁')
        created=False
        try:
            try:fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_RDWR|getattr(os,'O_BINARY',0),0o600);created=True
            except FileExistsError:
                if self.path.is_symlink():raise LockUnavailable('运行锁不是普通文件，需人工检查')
                fd=os.open(self.path,os.O_RDWR|getattr(os,'O_BINARY',0))
            self.fd=fd
            os.lseek(fd,0,os.SEEK_SET)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            os.lseek(fd,0,os.SEEK_SET);old=os.read(fd,256)
            if not created and not old.startswith(_MARKER):
                text=old.strip()
                if not text.isdigit() or not 0<int(text)<2**32:
                    raise LockUnavailable('旧运行锁无法安全确认，请先核对旧实例')
                active=_legacy_pid_active(int(text))
                if active is not False:
                    raise LockUnavailable('旧实例仍运行或无法确认状态，请先核对')
            os.lseek(fd,0,os.SEEK_SET);os.ftruncate(fd,0);os.write(fd,_MARKER+str(os.getpid()).encode('ascii')+b'\n');os.fsync(fd)
            return self
        except LockUnavailable:
            self.release();raise
        except OSError as error:
            self.release();raise LockUnavailable('已有实例或运行锁不可用，请先核对（系统错误 '+str(getattr(error,'winerror',None) or error.errno)+'）') from None
    def release(self):
        if self.fd is not None:
            os.close(self.fd);self.fd=None
