import json
import unittest
from bili_workbench.ws_event_candidate import parse_preview

class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.event={'self_id':1,'user_id':2,'message_id':3,'post_type':'message','message_type':'private','message':[{'type':'text','data':{'text':'fixture'}}]}
    def parse(self, event): return parse_preview(json.dumps(event).encode(),1,{2})
    def test_preview_never_grants_trust(self):
        result=self.parse(self.event)
        self.assertFalse(result['authenticated']); self.assertFalse(result['dispatch_allowed']); self.assertFalse(result['send_allowed'])
    def test_rejects_wrong_scope_and_duplicate_keys(self):
        self.event['user_id']=9
        with self.assertRaises(ValueError): self.parse(self.event)
        with self.assertRaises(ValueError): parse_preview(b'{"self_id":1,"self_id":1}',1,{2})
    def test_requires_explicit_allowlist_and_bounded_frames(self):
        with self.assertRaises(ValueError): parse_preview(b'{}',1,set())
        with self.assertRaises(ValueError): parse_preview(b'x'*65537,1,{2})
