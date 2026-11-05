import hashlib,hmac,json,tempfile,unittest
from bili_workbench.ws_event_candidate import compatibility_preview
from bili_workbench.qq_transport import OneBotHTTP,TransportConfig
from bili_workbench.runtime import State,Fault,canonical
from test_workbench import FakeHTTP

class LegacyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.event={'time':1000,'self_id':10001,'user_id':20002,'message_id':'23','post_type':'message','message_type':'private','sub_type':'friend','message':[{'type':'text','data':{'text':'模拟消息'}}]}
    def preview(self):return compatibility_preview(canonical(self.event).encode(),10001,{20002},now=1000)
    def test_normalization_never_grants_trust_or_send(self):
        result=self.preview();self.assertTrue(result['shape_compatible']);self.assertEqual(result['candidate']['message_id'],23)
        self.assertFalse(result['authenticated']);self.assertFalse(result['dispatch_allowed']);self.assertFalse(result['send_allowed']);self.assertFalse(result['actual_bridge_compatibility_verified'])
        with tempfile.TemporaryDirectory() as d:
            state=State(d);http=FakeHTTP();cfg=TransportConfig(endpoint='http://127.0.0.1:9876',bot_id=10001,owner_id=20002,access_token='fixture-access-token-0000',event_secret='fixture-event-secret-0000')
            try:
                adapter=OneBotHTTP(state,cfg,http=http,clock=lambda:1000);body=canonical(result['candidate']).encode();headers={'content-type':'application/json','x-self-id':'10001'}
                with self.assertRaises(Fault):adapter.verify_event(body,headers)
                headers['x-signature']='sha1='+hmac.new(cfg.event_secret.encode(),body,hashlib.sha1).hexdigest()
                verified=adapter.verify_event(body,headers);self.assertEqual(adapter.event_dict(verified)['message_id'],23);self.assertEqual(http.calls,[])
            finally:state.close()
    def test_does_not_invent_relationship_time_or_canonical_id(self):
        for key,value,reason in [('sub_type',None,'friend_subtype_required'),('time',None,'valid_event_time_required'),('time',1000-301,'valid_event_time_required'),('message_id','023','message_id_not_canonical_integer'),('message_id','9'*5000,'message_id_out_of_range')]:
            event=dict(self.event);event[key]=value
            result=compatibility_preview(canonical(event).encode(),10001,{20002},now=1000)
            self.assertFalse(result['shape_compatible']);self.assertIn(reason,result['reasons']);self.assertIsNone(result['candidate'])
    def test_unsupported_sources_and_invalid_frames_stay_rejected(self):
        for change in ({'user_id':20003},{'message_type':'group'},{'message':[{'type':'image','data':{}}]}):
            e=dict(self.event,**change)
            with self.assertRaises(ValueError):compatibility_preview(canonical(e).encode(),10001,{20002},now=1000)
        with self.assertRaises(ValueError):compatibility_preview(b'{"self_id":10001,"self_id":10001}',10001,{20002},now=1000)

if __name__=='__main__':unittest.main()
