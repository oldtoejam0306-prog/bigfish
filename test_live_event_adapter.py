import tempfile, unittest
from pathlib import Path
from live_event_adapter import LiveEventAdapter

class LiveAdapterTest(unittest.TestCase):
    def test_persistent_replay_conflict_and_scoped_memory(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.sqlite'; policy={'mode':'allowlist','userIds':['20002']}
            a=LiveEventAdapter(p,10001,policy)
            a.store.add_memory('friend','local-owner','long','PRIVATE FIXTURE','test',1)
            event={'self_id':10001,'user_id':20002,'message_id':1,'post_type':'message','message_type':'private','message':[{'type':'text','data':{'text':'fixture'}}]}
            first=a.receive(event)
            self.assertEqual(first['memory_count'],0);self.assertEqual(first['model'],'MockModel');self.assertFalse(first['send'])
            a.close();a=LiveEventAdapter(p,10001,policy)
            self.assertEqual(a.receive(event)['status'],'duplicate')
            changed={**event,'message':[{'type':'text','data':{'text':'different'}}]}
            self.assertEqual(a.receive(changed)['status'],'conflict')
            self.assertEqual(a.receive({**event,'self_id':1})['status'],'rejected')
            self.assertEqual(a.receive({**event,'user_id':2})['status'],'rejected')
            self.assertEqual(a.receive({**event,'message_type':'group'})['status'],'rejected')
            a.close()
