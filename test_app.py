import concurrent.futures
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from app import BigFish, OwnerSession
from boundary import FixtureSubtitleProvider
from core import Core
from vtt_provider import LocalVttProvider

class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'data.db'
        self.p=FixtureSubtitleProvider({'v':'字幕'}); self.a=BigFish(self.path,self.p,self.p.revision); self.s=self.a.local_owner_session()
    def tearDown(self): self.a.close(); self.tmp.cleanup()
    def test_no_scope_claim(self):
        with self.assertRaises(PermissionError): OwnerSession(self.a,('friend','victim'))
        with self.assertRaises(TypeError): self.s.memories('friend','victim')
        with self.assertRaises(TypeError): self.s.change_memory('friend','victim',1,'attacker','new')
        self.assertFalse(hasattr(self.a,'memories'))
        self.a._store.add_memory('friend','victim','long','secret','owner',1)
        self.assertEqual(self.s.memories(),[])
    def test_persistent_scoped_correction_delete(self):
        i=self.s.remember('long','原文','主人',0.7); self.s.change_memory(i,'纠正','新值')
        self.a.close(); self.a=BigFish(self.path,self.p,self.p.revision); self.s=self.a.local_owner_session()
        self.assertEqual(self.s.memories()[0]['content'],'新值'); self.s.change_memory(i,'删除'); self.assertEqual(self.s.memories(),[])
    def test_migration_explicit_and_atomic(self):
        self.a._core.db.execute("INSERT INTO memories(layer,content,source,confidence,created) VALUES ('long','known','user',0.8,'2020')")
        self.a._core.db.execute("INSERT INTO memories(layer,content,source,confidence,created) VALUES ('long','unknown','user',0.8,'2020')")
        self.a._core.db.commit(); self.assertEqual(self.s.memories(),[])
        self.a._store.migrate_legacy({1:('friend','local-owner')}); self.a._store.migrate_legacy({1:('friend','local-owner')})
        self.assertEqual([m['content'] for m in self.s.memories()],['known'])
        with self.assertRaises(KeyError): self.a._store.migrate_legacy({2:('friend','local-owner'),999:('friend','local-owner')})
        self.assertEqual([m['content'] for m in self.s.memories()],['known'])
        with self.assertRaises(ValueError): self.a._store.migrate_legacy({1:('group','1')})
        self.s.change_memory(self.s.memories()[0]['id'],'delete')
        self.assertEqual(self.a._core.db.execute('select content from memories where id=1').fetchone()[0],'known')
    def test_spy_budget_cache_uncertain(self):
        class Spy:
            def __init__(self): self.count=0; self.fail=False
            def get_subtitle(self,vid):
                from boundary import Subtitle
                self.count+=1
                if self.fail: raise RuntimeError('unknown outcome')
                return Subtitle(vid,'字幕','spy')
        spy=Spy(); self.a.close(); self.a=BigFish(self.path,spy,'immutable-r1'); self.s=self.a.local_owner_session()
        with self.assertRaises(ValueError): self.s.read_fixture('v','id',simulated_cost=1)
        self.assertEqual(spy.count,0); self.a._store.set_budget(3)
        self.assertFalse(self.s.read_fixture('v','id',simulated_cost=1)['cached']); self.assertEqual(spy.count,1)
        self.assertTrue(self.s.read_fixture('v','id',simulated_cost=1)['cached']); self.assertEqual(spy.count,1)
        self.assertTrue(self.s.read_fixture('v','other',simulated_cost=1)['cached']); self.assertEqual(spy.count,1)
        spy.fail=True
        with self.assertRaises(RuntimeError): self.s.read_fixture('v2','new',simulated_cost=1)
        with self.assertRaises(RuntimeError): self.s.read_fixture('v2','new',simulated_cost=1)
        self.assertEqual(spy.count,2); self.assertEqual(self.a._store._used(),2)
    def test_vtt_real_upstream(self):
        vtt='WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n你好 <b>朋友</b>\n'
        p=LocalVttProvider('v',vtt); self.a.close(); self.a=BigFish(self.path,p,p.revision); self.s=self.a.local_owner_session()
        r=self.s.read_fixture('v','x'); self.assertEqual(r['untrusted_subtitle'],'你好 朋友'); self.assertEqual(r['coverage'],'subtitle_only')
        self.assertNotEqual(p.revision,LocalVttProvider('v',vtt+'\n').revision)
    def test_cli_persists(self):
        def run(text): return subprocess.run([sys.executable,'cli.py','--db',str(self.path)],input=text,text=True,capture_output=True,check=True).stdout
        run('记住 持久化内容\n退出\n'); self.assertIn('持久化内容',run('记忆\n退出\n'))
    def test_cli_missing_subtitle_keeps_running_without_reservation(self):
        run=subprocess.run([sys.executable,'cli.py','--db',str(self.path)],input='读字幕 r\n读字幕 r\n记住 仍可继续\n记忆\n退出\n',text=True,capture_output=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.count('未指定字幕文件'),2)
        self.assertIn('仍可继续',run.stdout)
        self.assertEqual(self.a._store.db.execute('select count(*) from reservations').fetchone()[0],0)
        self.assertEqual(self.a._store.db.execute('select count(*) from executions').fetchone()[0],0)
    def test_cli_malformed_subtitle_keeps_running_and_retains_uncertainty(self):
        subtitle=Path(self.tmp.name)/'bad.vtt'; subtitle.write_text('not WebVTT',encoding='utf-8')
        run=subprocess.run([sys.executable,'cli.py','--db',str(self.path),'--subtitle',str(subtitle)],input='读字幕 r\n读字幕 r\n记住 继续工作\n记忆\n退出\n',text=True,capture_output=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('字幕格式无效',run.stdout)
        self.assertIn('请求无法继续',run.stdout)
        self.assertIn('继续工作',run.stdout)
        self.assertEqual(self.a._store.db.execute("select state from reservations").fetchone()[0],'reserved')
        self.assertEqual(self.a._store.db.execute("select state from executions").fetchone()[0],'started')
    def test_time_validation_and_old_bad_record(self):
        c=self.a._core
        with self.assertRaises(ValueError): c.state('emotion','happy',1,'2026-01-01')
        c.db.execute("INSERT INTO states VALUES ('emotion','old',1,'2026-01-01')"); c.db.commit()
        with self.assertRaisesRegex(ValueError,'人工修复'): c.read_state('emotion','old')
        c.state('emotion','ok',1,'2026-01-01T08:00:00+08:00')
        self.assertAlmostEqual(c.read_state('emotion','ok','2026-01-01T01:00:00+00:00'),0.5)
    def test_personality_real_connections(self):
        barrier=threading.Barrier(2)
        def run(n):
            c=Core(self.path)
            try:
                p=c.personality()['data']; p['creative_interest']=str(n); barrier.wait()
                return c.correct('用户纠正'+str(n),p)
            finally: c.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,[1,2]))
        self.assertEqual(sorted(results),[2,3])
        self.assertEqual(self.a._core.db.execute('select count(*) from corrections').fetchone()[0],2)

if __name__=='__main__': unittest.main()
