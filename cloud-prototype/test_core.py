import tempfile
import unittest
from pathlib import Path
from core import Core

class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'data.db'; self.c=Core(self.path)
    def tearDown(self): self.c.close(); self.tmp.cleanup()
    def test_persistence_and_correction(self):
        p=self.c.personality()['data']; p['creative_interest']='绘画'
        self.assertEqual(self.c.correct('纠正兴趣',p),2)
        self.c.close(); self.c=Core(self.path)
        self.assertEqual(self.c.personality()['data']['creative_interest'],'绘画')
        self.assertEqual(self.c.db.execute('SELECT before_version,after_version FROM corrections').fetchone()[:],(1,2))
    def test_legacy_memory_disabled(self):
        for fn,args in [(self.c.remember,('long','x','y',0.5)),(self.c.memories,()),(self.c.delete_memory,(1,))]:
            with self.assertRaises(RuntimeError): fn(*args)
    def test_decay(self):
        self.c.state('emotion','快乐',1,'2026-01-01T00:00:00+00:00')
        self.c.state('interest','画画',1,'2026-01-01T00:00:00+00:00')
        self.assertAlmostEqual(self.c.read_state('emotion','快乐','2026-01-01T01:00:00+00:00'),0.5)
        self.assertAlmostEqual(self.c.read_state('interest','画画','2026-01-31T00:00:00+00:00'),0.5)
    def test_access_and_no_send(self):
        self.assertFalse(self.c.receive_simulated('friend','1','hi')['accepted'])
        self.c.allow('group','1'); self.assertFalse(self.c.receive_simulated('friend','1','hi')['accepted'])
        self.assertTrue(self.c.receive_simulated('group','1','hi')['accepted'])
        self.c.set_policy('external_send',True)
        with self.assertRaises(NotImplementedError): self.c.send('group','1','hi')
    def test_activity(self):
        self.assertEqual(self.c.schedule('探索'),'未授权')
        self.c.set_policy('activity_authorized',True); self.c.set_policy('rest',True)
        self.assertEqual(self.c.schedule('探索'),'休息中')
        self.c.set_policy('rest',False); self.c.set_policy('activity_limit',1)
        self.assertEqual(self.c.schedule('探索',1),'付费活动尚未实现')
        self.assertEqual(self.c.schedule('探索'),'已安排离线模拟活动')
        self.assertEqual(self.c.schedule('探索'),'今日活动次数已达上限')
    def test_video_cache_and_coverage(self):
        r=self.c.video_route('a','字幕',True); self.assertEqual(r['coverage'],'仅字幕'); self.assertFalse(r['watched_video'])
        self.assertTrue(self.c.video_route('a','字幕',True)['cached'])
        self.assertFalse(self.c.video_route('a','字幕',False)['cached'])
        self.assertIn('升级',self.c.video_route('a','字幕',difficult=True)['stage'])
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)
    def test_accounting_and_voice(self):
        self.c.record_usage('example','model','2026-01',100,50,0.01)
        self.assertEqual(self.c.db.execute('SELECT cost FROM ledger').fetchone()[0],0.01)
        self.assertEqual(self.c.voice_parameters('gentle')['voice_id'],self.c.voice_parameters('excited')['voice_id'])
        self.assertEqual(self.c.policy('paid_budget'),0)

if __name__=='__main__': unittest.main()
