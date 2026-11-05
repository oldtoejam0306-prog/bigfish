import hashlib
import hmac
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from bili_workbench.runtime import State,Fault,canonical
from bili_workbench.qq_transport import OneBotHTTP,TransportConfig,TrustedEvent

class MockHTTP:
    def __init__(self):self.calls=[];self.bot=10001;self.receipt={'status':'ok','retcode':0,'data':{'message_id':-123}};self.fail=False;self.cancel=None
    def json(self,method,url,headers=None,payload=None):
        self.calls.append((method,url,headers,payload))
        if url.endswith('/get_login_info'):
            if self.cancel:self.cancel()
            return {'status':'ok','retcode':0,'data':{'user_id':self.bot}}
        if self.fail:raise OSError('mock secret error body fixture-access-token')
        return self.receipt

class QQTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=State(self.tmp.name);self.s.gate=lambda *a,**kw:None;self.http=MockHTTP()
        self.cfg=TransportConfig('http://127.0.0.1:5700',10001,20002,'fixture-access-token','fixture-event-secret')
        self.t=OneBotHTTP(self.s,self.cfg,self.http,clock=lambda:1000000.)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def event(self,**changes):
        value={'time':1000000,'self_id':10001,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'user_id':20002,'message':[{'type':'text','data':{'text':'你好'}}]}
        value.update(changes);return value
    def signed(self,event=None,body=None,adapter=None):
        raw=canonical(event or self.event()).encode() if body is None else body
        config=(adapter or self.t).c
        headers={'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(config.event_secret.encode(),raw,hashlib.sha1).hexdigest()}
        return raw,headers
    def trusted(self,**changes):return self.t.verify_event(*self.signed(self.event(**changes)))
    def candidate(self,echo='reply:one',user=20002,text='你好，我是大肥鱼。'):
        return {'action':'send_private_msg','params':{'user_id':user,'message':[{'type':'text','data':{'text':text}}]},'echo':echo}
    def enabled(self,**changes):
        self.cfg=replace(self.cfg,enabled=True,transport_validated=True,**changes);self.t=OneBotHTTP(self.s,self.cfg,self.http,clock=lambda:1000000.);return self.t
    def test_changed_endpoint_cannot_reroute_existing_draft(self):
        self.enabled();draft=self.t.prepare(self.candidate(),local_authorization=True)
        changed=OneBotHTTP(self.s,replace(self.cfg,endpoint='http://127.0.0.1:5701'),self.http,clock=lambda:1000000.)
        with self.assertRaises(Fault):changed.send(draft['id'],'确认发送')
        self.assertEqual(self.http.calls,[])
    def test_transport_error_is_unknown_and_does_not_echo_secret(self):
        self.enabled();draft=self.t.prepare(self.candidate(),self.trusted());self.http.fail=True
        with self.assertRaises(Fault) as error:self.t.send(draft['id'],'确认发送')
        self.assertNotIn('fixture-access-token',str(error.exception))
        self.assertEqual(self.t.inspect(draft['id'])['state'],'uncertain')
        calls=len(self.http.calls)
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        self.assertEqual(len(self.http.calls),calls)
    def test_signed_event_is_opaque_and_external_mutation_does_not_change_origin(self):
        event=self.trusted();self.assertFalse(event.replayed);copy=event.event();copy['user_id']=999
        self.assertEqual(self.t.event_dict(event)['user_id'],20002)
        self.assertTrue(self.t.verify_event(*self.signed()).replayed)
        other=OneBotHTTP(self.s,self.cfg,self.http,clock=lambda:1000000.)
        with self.assertRaises(Fault):other.event_dict(event)
        self.assertEqual(self.http.calls,[])
    def test_unsigned_forged_or_conflicting_event_never_reaches_api(self):
        raw,headers=self.signed();headers['X-Signature']='sha1='+'0'*40
        with self.assertRaises(Fault):self.t.verify_event(raw,headers)
        self.trusted()
        changed=self.event(message=[{'type':'text','data':{'text':'不同内容'}}])
        with self.assertRaises(Fault):self.t.verify_event(*self.signed(changed))
        self.assertEqual(self.http.calls,[])
    def test_scope_identity_expiry_duplicates_and_attachments_rejected(self):
        cases=[self.event(self_id=999),self.event(user_id=30003),self.event(time=999000),self.event(message=[{'type':'image','data':{'file':'private.txt'}}])]
        for event in cases:
            with self.assertRaises(Fault):self.t.verify_event(*self.signed(event))
        duplicate=b'{"self_id":10001,"self_id":999}'
        with self.assertRaises(Fault):self.t.verify_event(*self.signed(body=duplicate))
        self.assertEqual(self.http.calls,[])
    def test_default_sender_disabled_and_dry_run_zero_network(self):
        draft=self.t.prepare(self.candidate(),self.trusted());self.assertTrue(draft['dry_run'])
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        self.assertEqual(self.http.calls,[])
    def test_recipient_cannot_be_changed_from_verified_origin(self):
        broad=replace(self.cfg,owner_test_only=False);self.t=OneBotHTTP(self.s,broad,self.http,permission=lambda kind,peer:True,clock=lambda:1000000.)
        origin=self.trusted()
        with self.assertRaises(Fault):self.t.prepare(self.candidate(user=30003),origin)
        with self.assertRaises(Fault):self.t.prepare(self.candidate())
        self.assertEqual(self.http.calls,[])
    def test_single_send_acknowledges_api_not_delivery_and_never_replays(self):
        self.enabled();origin=self.trusted();draft=self.t.prepare(self.candidate(),origin)
        result=self.t.send(draft['id'],'确认发送')
        self.assertTrue(result['api_acknowledged']);self.assertFalse(result['delivery_confirmed']);self.assertEqual(result['message_id'],-123)
        self.assertEqual(len(self.http.calls),2)
        self.assertEqual(self.http.calls[-1][2]['Authorization'],'Bearer fixture-access-token')
        self.assertNotIn('access_token=',self.http.calls[-1][1])
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        self.assertEqual(len(self.http.calls),2)
        self.assertNotIn('fixture-access-token',canonical(self.t.inspect(draft['id'])))
    def test_ambiguous_async_bad_boolean_and_timeout_do_not_replay(self):
        for index,receipt in enumerate(({'status':'async','retcode':1,'data':None},{'status':'ok','retcode':False,'data':{'message_id':123}},{'status':'ok','retcode':0,'data':{'message_id':True}})):
            self.enabled();self.http.receipt=receipt;draft=self.t.prepare(self.candidate(echo='reply:'+str(index)),local_authorization=True)
            with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
            self.assertEqual(self.t.inspect(draft['id'])['state'],'uncertain')
            n=len(self.http.calls)
            with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
            self.assertEqual(len(self.http.calls),n)
    def test_wrong_login_identity_stops_before_write(self):
        self.enabled();self.http.bot=999;draft=self.t.prepare(self.candidate(),self.trusted())
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        self.assertEqual(self.t.inspect(draft['id'])['state'],'rejected_identity')
        self.assertEqual(len(self.http.calls),1);self.assertTrue(self.http.calls[0][1].endswith('/get_login_info'))
    def test_cancel_after_identity_never_calls_send_api(self):
        self.enabled();cancelled=[False];self.http.cancel=lambda:cancelled.__setitem__(0,True)
        draft=self.t.prepare(self.candidate(),self.trusted())
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送',cancel_check=lambda:cancelled[0])
        self.assertEqual(self.t.inspect(draft['id'])['state'],'cancelled_not_sent');self.assertEqual(len(self.http.calls),1)
    def test_zero_attempt_quota_and_secret_text_fail_before_network(self):
        self.enabled(daily_limit=0)
        with self.assertRaises(Fault):self.t.prepare(self.candidate(text='fixture-access-token'),local_authorization=True)
        draft=self.t.prepare(self.candidate(),local_authorization=True)
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        self.assertEqual(self.http.calls,[])
        self.assertNotIn('fixture-event-secret',repr(self.cfg));self.assertNotIn('fixture-access-token',repr(self.cfg))
    def test_unsafe_endpoint_and_non_text_actions_are_rejected(self):
        for endpoint in ('http://localhost:5700','http://evil.test:5700','http://127.0.0.1:5700/path','http://127.0.0.1:5700/?token=secret','http://user:pass@127.0.0.1:5700'):
            with self.assertRaises(Fault):replace(self.cfg,endpoint=endpoint).validate()
        c=self.candidate();c['params']['message']=[{'type':'at','data':{'qq':'all'}}]
        with self.assertRaises(Fault):self.t.prepare(c,local_authorization=True)
        self.assertEqual(self.http.calls,[])
    def test_group_social_candidate_supported_when_explicit_policy_allows(self):
        config=replace(self.cfg,owner_test_only=False)
        t=OneBotHTTP(self.s,config,self.http,permission=lambda kind,peer:kind=='group' and peer==40004,clock=lambda:1000000.)
        event=self.event(message_type='group',sub_type='normal',group_id=40004,user_id=30003)
        origin=t.verify_event(*self.signed(event,adapter=t));candidate={'action':'send_group_msg','params':{'group_id':40004,'message':[{'type':'text','data':{'text':'可以讨论创作'}}]},'echo':'group:one'}
        draft=t.prepare(candidate,origin);self.assertEqual(t.inspect(draft['id'])['params']['group_id'],40004)
        self.assertEqual(t.inspect(draft['id'])['params']['message'][0]['data']['text'],'可以讨论创作')
        self.assertEqual(self.http.calls,[])
    def test_restart_quarantines_in_flight_and_same_id_conflict(self):
        draft=self.t.prepare(self.candidate(),self.trusted())
        self.s.db.execute("UPDATE qq_transport_outbox SET state='sending' WHERE id=?",(draft['id'],))
        self.enabled()
        self.assertEqual(self.t.inspect(draft['id'])['state'],'uncertain')
        with self.assertRaises(Fault):self.t.send(draft['id'],'确认发送')
        with self.assertRaises(Fault):self.t.prepare(self.candidate(text='其他'),local_authorization=True)
        self.assertEqual(self.http.calls,[])
    def test_two_connections_cannot_double_send(self):
        self.enabled();self.t.prepare(self.candidate(),local_authorization=True)
        second=State(self.tmp.name);second.gate=lambda *a,**kw:None
        other=OneBotHTTP(second,self.cfg,self.http,clock=lambda:1000000.)
        outcomes=[]
        def run(adapter):
            try:outcomes.append(adapter.send('reply:one','确认发送'))
            except Fault:outcomes.append('blocked')
        try:
            threads=[threading.Thread(target=run,args=(adapter,)) for adapter in (self.t,other)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=5)
            self.assertEqual(sum(isinstance(outcome,dict) for outcome in outcomes),1)
            self.assertEqual(sum(url.endswith('/send_private_msg') for method,url,*rest in self.http.calls),1)
        finally:second.close()

if __name__=='__main__':unittest.main()
