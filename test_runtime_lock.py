import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bili_workbench.runtime_lock import RuntimeLock,LockUnavailable

class RuntimeLockTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'runtime.lock'
    def tearDown(self):self.tmp.cleanup()
    def test_second_instance_blocked_and_release_reusable(self):
        a=RuntimeLock(self.path).acquire();b=RuntimeLock(self.path)
        try:
            with self.assertRaises(LockUnavailable):b.acquire()
        finally:a.release()
        b.acquire();b.release()
    def test_legacy_live_or_unverifiable_lock_fails_closed(self):
        for status in (True,None):
            self.path.write_text('12345',encoding='ascii')
            with patch('bili_workbench.runtime_lock._legacy_pid_active',return_value=status):
                with self.assertRaises(LockUnavailable):RuntimeLock(self.path).acquire()
            self.assertEqual(self.path.read_text(encoding='ascii'),'12345')
    def test_legacy_absent_pid_is_safely_migrated(self):
        self.path.write_text('12345',encoding='ascii')
        with patch('bili_workbench.runtime_lock._legacy_pid_active',return_value=False):
            lock=RuntimeLock(self.path).acquire();lock.release()
        self.assertTrue(self.path.read_bytes().startswith(b'BIGFISH_LOCK_V1\n'))
    def test_invalid_legacy_lock_not_overwritten(self):
        self.path.write_text('invalid',encoding='ascii')
        with self.assertRaises(LockUnavailable):RuntimeLock(self.path).acquire()
        self.assertEqual(self.path.read_text(encoding='ascii'),'invalid')
    def test_abrupt_child_exit_releases_process_lock(self):
        program="from bili_workbench.runtime_lock import RuntimeLock;import sys,time; x=RuntimeLock(sys.argv[1]).acquire();print('READY',flush=True);time.sleep(120)"
        child=subprocess.Popen([sys.executable,'-c',program,str(self.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'READY')
            with self.assertRaises(LockUnavailable):RuntimeLock(self.path).acquire()
        finally:child.kill();child.wait(timeout=10);child.stdout.close();child.stderr.close()
        lock=RuntimeLock(self.path).acquire();lock.release()

if __name__=='__main__':unittest.main()
