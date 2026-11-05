"""Reproducible end-to-end fixture check. Never opens a network connection.
Uses an isolated temporary directory, synthetic identities and fixture pricing.
"""
import json
import tempfile
from .runtime import Fault
from .service import Workbench

BV='BV1q2nW6DEaf'
class FixtureVault:
    """Memory-only fixture values; never accesses the real credential store."""
    def __init__(self): self.values={}
    def save(self, data): self.values=dict(data)
    def read(self): return dict(self.values)

class FixtureHTTP:
    def __init__(self):self.calls=[];self.usage=True
    def json(self,method,url,headers=None,payload=None,form=False):
        self.calls.append((method,url));
        if url=='https://fixture.invalid/v1/chat/completions':
            result={'choices':[{'message':{'content':'【模拟模型结果】仅用于离线验收。'}}]}
            if self.usage:result['usage']={'prompt_tokens':100,'completion_tokens':20}
            return result
        if '/x/web-interface/nav' in url:return {'code':0,'data':{'wbi_img':{'img_url':'https://fixture/'+('a'*32)+'.png','sub_url':'https://fixture/'+('b'*32)+'.png'}}}
        if '/x/web-interface/wbi/view' in url:return {'code':0,'data':{'bvid':BV,'aid':1,'cid':2,'title':'离线素材'}}
        if '/x/player/wbi/v2' in url:return {'code':0,'data':{'subtitle':{'subtitles':[{'lan':'zh','subtitle_url':'https://aisubtitle.hdslb.com/fixture.json'}]}}}
        if url=='https://aisubtitle.hdslb.com/fixture.json':return {'body':[{'from':0,'to':1,'content':'这是模拟字幕，不是真实读取结果。'}]}
        raise Fault('fixture不支持此动作，禁止网络回退')
    def request(self,*args,**kwargs):raise Fault('fixture不允许底层网络/外发请求')

def run():
    checks=dict.fromkeys(['default_denies','other_friend_denies','owner_personality_model_draft','persistent_dedup','bili_real_routing_fixture_response','shared_ledger','unknown_usage_no_retry','no_outbound','multi_turn_history','social_public_scope','group_public_scope','interest_selection','creation_plan_no_api'],False);transport=FixtureHTTP()
    with tempfile.TemporaryDirectory(prefix='bigfish-preflight-') as root:
        w=Workbench(root,transport)
        try:
            w.s.vault=FixtureVault()
            w.s.gate=lambda *args,**kwargs:None
            event={'self_id':10001,'user_id':20002,'post_type':'message','message_type':'private','sub_type':'friend','message_id':1,'message':[{'type':'text','data':{'text':'离线验收'}}]}
            try:w.qq.process_fixture(event)
            except Fault:checks['default_denies']=len(transport.calls)==0
            w.qq.configure({'bot_id':10001,'owner_id':20002,'enabled':True,'social_mode':'owner_only'})
            w.s.configure({'read_enabled':True,'provider_url':'https://fixture.invalid/v1','model':'fixture','paid_enabled':True,'daily_budget_micro':100000,'request_cap_micro':10000,'price_version':'fixture-not-real-pricing','input_price_micro_per_million':200000,'output_price_micro_per_million':800000})
            w.s.vault.save({'api_key':'fixture-placeholder-not-a-credential'})
            wrong=dict(event,user_id=999)
            try:w.qq.process_fixture(wrong)
            except Fault:checks['other_friend_denies']=len(transport.calls)==0
            draft=w.qq.process_fixture(event);before=len(transport.calls)
            checks['owner_personality_model_draft']=draft['status']=='model_draft' and draft['context_personality_version']==1 and not draft['send']
            duplicate=w.qq.process_fixture(event)
            checks['persistent_dedup']=duplicate['status']=='duplicate_or_uncertain' and len(transport.calls)==before
            follow=w.qq.process_fixture(dict(event,message_id=3,message=[{'type':'text','data':{'text':'继续离线模拟'}}]))
            checks['multi_turn_history']=follow.get('history_turns_used')==2
            w.qq.configure({'social_mode':'account_social'})
            friend=w.qq.process_fixture(dict(event,user_id=30003,message_id=4));checks['social_public_scope']=friend.get('audience')=='public' and not friend['send'] and friend['context_memory_count']==0
            group=dict(event,user_id=30003,message_id=5,message_type='group',sub_type='normal',group_id=40004,message=[{'type':'at','data':{'qq':'10001'}},{'type':'text','data':{'text':'群聊离线模拟'}}])
            group_result=w.qq.process_fixture(group);checks['group_public_scope']=group_result.get('audience')=='public' and group_result['reply_candidate']['action']=='send_group_msg' and group_result['history_turns_used']==0
            w.interests.correct('绘画',1,'合成角色公开兴趣，不是主人私人偏好')
            choice=w.interests.select([{'bvid':BV,'title':'绘画教程'},{'bvid':'BV0000000001','title':'数学教程'}],max_items=1,explore_ratio=0)
            checks['interest_selection']=choice['items'][0]['bvid']==BV and choice['model_called'] is False
            creation=w.creation.plan({'title':'合成原创试片','public_use_confirmed':True,'scenes':[{'text':'AI原创fixture','duration':1,'color':'#112233','source':'合成纯色与文字'}]})
            checks['creation_plan_no_api']=creation['network_called'] is False and creation['published'] is False
            plan=w.video_preparation.prepare(BV,'subtitle',1)
            video=w.video_preparation.execute(plan['id'],'确认最多调用'+str(plan['max_model_calls'])+'次模型')['result']
            checks['bili_real_routing_fixture_response']=video['result']['coverage']=='仅字幕' and not video['result']['full_video_watched']
            checks['shared_ledger']=w.s.db.execute('SELECT count(*) FROM charges').fetchone()[0]==5
            transport.usage=False
            second=dict(event,message_id=2,message=[{'type':'text','data':{'text':'新文本未知用量'}}])
            unknown=w.qq.process_fixture(second);before=len(transport.calls)
            duplicate=w.qq.process_fixture(second,retry_blocked=True)
            checks['unknown_usage_no_retry']=unknown['status']=='uncertain' and duplicate['previous_state']=='uncertain' and len(transport.calls)==before
            checks['no_outbound']=not w.s.config()['outbound_enabled'] and all(method=='GET' or url=='https://fixture.invalid/v1/chat/completions' for method,url in transport.calls)
        finally:w.close()
    return {'passed':bool(checks) and all(checks.values()),'checks':checks,'execution':'isolated offline fixtures, not real model/platform validation','credential_store':'in-memory fixture; OS credential protection not tested','network_connections':0,'sent_messages':0,'real_credentials_used':False,'windows_mac_tested':False}

def main():
    result=run();print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
