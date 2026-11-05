import hashlib,hmac,json,tempfile,time,unittest
from unittest.mock import patch
from bili_workbench.service import Workbench
from bili_workbench.runtime import Fault,canonical
from test_workbench import FakeHTTP,BV

class FlowHTTP(FakeHTTP):
    def json(self,method,url,headers=None,payload=None,form=False):
        if '/x/v2/reply?' in url:
            self.calls.append((method,url,headers,payload));return {'code':0,'data':{'replies':[{'rpid':9,'content':{'message':'一起画画吧'}}]}}
        return super().json(method,url,headers,payload,form)
class NativeAPI:
    def __init__(self):self.calls=[]
    def json(self,method,url,headers=None,payload=None,form=False):
        self.calls.append((method,url))
        if url.endswith('/get_login_info'):return {'status':'ok','retcode':0,'data':{'user_id':10001}}
        return {'status':'ok','retcode':0,'data':{'message_id':123}}
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FlowHTTP();self.w=Workbench(self.tmp.name,self.http);self.s=self.w.s;self.s.gate=lambda *a,**kw:None
        self.s.configure({'read_enabled':True,'account_mid':'123','provider_url':'https://example.test/v1','model':'cheap','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'input_price_micro_per_million':200000,'output_price_micro_per_million':800000,'price_version':'fixture-CNY','qq_endpoint':'http://127.0.0.1:9999'})
        self.s.vault.save({'api_key':'fixture-secret','qq_access_token':'fixture-access-token-0000','qq_event_secret':'fixture-event-secret-0000'})
        self.w.qq.configure({'enabled':True,'bot_id':10001,'owner_id':20002})
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def event(self,mid=1,text='你好'):return {'time':int(time.time()),'self_id':10001,'user_id':20002,'post_type':'message','message_type':'private','sub_type':'friend','message_id':mid,'message':[{'type':'text','data':{'text':text}}]}
    def signed(self,event):
        raw=canonical(event).encode();return raw,{'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(b'fixture-event-secret-0000',raw,hashlib.sha1).hexdigest()}
    def test_background_reaction_requires_all_flags_and_does_not_duplicate(self):
        self.s.configure({'qq_sender_enabled':True,'qq_transport_validated':True,'qq_auto_process':True,'qq_auto_send':True})
        api=NativeAPI();self.w.qq_transport().http=api
        raw,headers=self.signed(self.event());self.w.accept_qq_event(raw,headers)
        self.w.interaction.tick();self.assertEqual(self.w.interaction.state()['sent'],1)
        self.w.interaction.tick();self.assertEqual(len(api.calls),2);self.assertEqual(len(self.http.calls),1)
    def test_acceptance_profile_never_background_sends_and_pause_is_effective(self):
        self.w.qq.configure({'acceptance_owner_only':True})
        self.s.configure({'qq_sender_enabled':True,'qq_transport_validated':True,'qq_auto_process':True,'qq_auto_send':True})
        api=NativeAPI();self.w.qq_transport().http=api
        raw,headers=self.signed(self.event());self.w.accept_qq_event(raw,headers)
        self.w.interaction.tick();self.assertEqual(api.calls,[])
        self.w.interaction.pause();self.assertFalse(self.s.config()['qq_auto_process']);self.assertFalse(self.s.config()['qq_auto_send'])
    def test_verified_owner_memory_commands_work_without_model_or_key(self):
        self.s.configure({'qq_transport_validated':True,'paid_enabled':False,'provider_url':''})
        from bili_workbench.growth import Scope
        raw,headers=self.signed(self.event(text='/记住 我喜欢合成绘画'))
        self.w.accept_qq_event(raw,headers)
        context=self.w.growth.context(Scope.owner(),include_memory=True)
        self.assertEqual(context['memories'][0]['content'],'我喜欢合成绘画')
        self.w.accept_qq_event(raw,headers);self.assertEqual(len(self.w.growth.context(Scope.owner(),include_memory=True)['memories']),1)
        ident=context['memories'][0]['id'];raw,headers=self.signed(self.event(2,'/忘记记忆 '+str(ident)))
        self.w.accept_qq_event(raw,headers);self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'],[])
        self.assertEqual(self.http.calls,[])
    def test_received_unconfigured_message_can_retry_without_untrusted_source_restore(self):
        self.s.configure({'qq_transport_validated':True,'paid_enabled':False})
        raw,headers=self.signed(self.event());self.w.accept_qq_event(raw,headers)
        self.assertEqual(self.w.process_qq_inbox()['results'][0]['status'],'not_configured')
        ident=self.w.qq_inbox_status()['inbox'][0]['id'];self.s.configure({'paid_enabled':True})
        self.assertTrue(self.w.retry_qq_inbox(ident)['queued']);self.assertEqual(self.w.process_qq_inbox()['results'][0]['status'],'model_draft')
        self.assertEqual(len(self.http.calls),1)
    def test_owner_commands_need_verified_transport_not_fixture_claim(self):
        self.s.configure({'paid_enabled':False})
        result=self.w.qq.process_fixture(self.event(text='/记住 不能凭自报主人写入'))
        self.assertEqual(result['status'],'not_configured')
        from bili_workbench.growth import Scope
        self.assertEqual(self.w.growth.context(Scope.owner(),include_memory=True)['memories'],[])
    def test_local_transport_secret_cannot_enter_model_context(self):
        with self.assertRaises(Fault):self.w.model.chat('你好','must-not-leak',{},[{'content':'不要外传fixture-event-secret-0000'}])
        self.assertEqual(self.http.calls,[]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM charges').fetchone()[0],0)
    def test_fixture_history_is_multiturn_but_never_platform_auth(self):
        first=self.w.qq.process_fixture(self.event());second=self.w.qq.process_fixture(self.event(2,'继续聊'))
        self.assertEqual(second['history_turns_used'],2);self.assertFalse(first['platform_authenticated'])
        self.assertEqual(len(self.http.calls[-1][3]['messages']),4)
        with self.assertRaises(Fault):self.w.prepare_qq_reply(first['event_key'],'确认准备QQ回复')
    def test_native_signed_ingress_to_model_and_manual_disabled_sender(self):
        self.s.configure({'qq_transport_validated':True})
        raw,headers=self.signed(self.event());self.w.accept_qq_event(raw,headers)
        self.assertEqual(self.http.calls,[])
        result=self.w.process_qq_inbox()['results'][0]
        self.assertTrue(result['transport_authenticated']);self.assertFalse(result['platform_authenticated']);self.assertFalse(result['fixture']);self.assertEqual(result['history_turns_used'],0)
        with self.assertRaises(Fault):self.w.send_qq(result['transport_draft']['id'],'确认发送')
        self.assertEqual(len(self.http.calls),1)
    def test_native_ack_records_only_confirmed_assistant_history_no_double_send(self):
        self.s.configure({'qq_sender_enabled':True,'qq_transport_validated':True})
        api=NativeAPI();transport=self.w.qq_transport();transport.http=api
        raw,headers=self.signed(self.event());self.w.accept_qq_event(raw,headers);result=self.w.process_qq_inbox()['results'][0]
        receipt=self.w.send_qq(result['transport_draft']['id'],'确认发送');self.assertTrue(receipt['api_acknowledged']);self.assertFalse(receipt['delivery_confirmed'])
        with self.assertRaises(Fault):self.w.send_qq(result['transport_draft']['id'],'确认发送')
        raw,headers=self.signed(self.event(2,'第二条'));self.w.accept_qq_event(raw,headers);next_result=self.w.process_qq_inbox()['results'][0]
        self.assertEqual(next_result['history_turns_used'],2);self.assertEqual(len(api.calls),2)
    def test_native_and_fixture_histories_are_isolated(self):
        self.s.configure({'qq_transport_validated':True})
        self.w.qq.process_fixture(self.event(text='离线fixture不应进入真实历史'))
        raw,headers=self.signed(self.event(2));self.w.accept_qq_event(raw,headers)
        result=self.w.process_qq_inbox()['results'][0];self.assertEqual(result['history_turns_used'],0)
        self.assertNotIn('离线fixture不应进入真实历史',canonical(self.http.calls[-1][3]))
    def test_comment_reply_is_public_scoped_draft_and_parent_is_bound(self):
        result=self.w.activities.comment_reply(BV,'9','comment-1')
        self.assertEqual(result['status'],'drafted');self.assertFalse(result['published'])
        draft=self.w.draft_detail(result['draft']['id']);self.assertEqual(draft['payload']['root'],'9');self.assertEqual(draft['payload']['parent'],'9')
        self.assertTrue(draft['payload']['text'].startswith('【AI·大肥鱼】'))
        n=len(self.http.calls);self.w.activities.comment_reply(BV,'9','comment-1');self.assertEqual(len(self.http.calls),n)
    def test_model_script_is_only_proposal_no_files_or_paths(self):
        original=self.http.json
        def script(*a,**kw):
            value=original(*a,**kw)
            if a[1].endswith('/chat/completions'):value['choices'][0]['message']['content']=json.dumps({'title':'原创','scenes':[{'text':'我是AI大肥鱼','duration':5,'color':'#112233'}]})
            return value
        self.http.json=script;proposal=self.w.activities.creation_script('绘画','script-1')
        self.assertEqual(proposal['status'],'proposal');self.assertFalse(proposal['script']['public_use_confirmed'])
        with self.assertRaises(Fault):self.w.creation.plan(proposal['script'])
        approved=dict(proposal['script'],public_use_confirmed=True);plan=self.w.creation.plan(approved);self.assertIn('id',plan)

if __name__=='__main__':unittest.main()
