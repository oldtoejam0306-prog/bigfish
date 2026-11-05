import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from core import Core,INITIAL
from bili_workbench.runtime import Fault
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class GrowthReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
        self.proposal=self.w.growth_action({'action':'propose_personality','instruction':'模拟成长','personality':dict(INITIAL)})
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def test_review_and_reject_do_not_change_personality_or_call_platform(self):
        listing=self.w.growth_action({'action':'list_personality'})
        self.assertEqual(listing['proposals'][0]['id'],self.proposal['id']);self.assertFalse(listing['automatically_applied'])
        with self.assertRaises(Fault):self.w.growth_action({'action':'reject_personality','id':self.proposal['id'],'confirmation':'不正确'})
        result=self.w.growth_action({'action':'reject_personality','id':self.proposal['id'],'confirmation':'放弃成长提案'})
        self.assertEqual(result['state'],'rejected');self.assertEqual(self.w.growth_action({'action':'list_personality'})['proposals'],[])
        with self.assertRaises(Fault):self.w.growth_action({'action':'apply_personality','id':self.proposal['id'],'confirmation':'应用成长'})
        core=Core(Path(self.tmp.name)/'character.db')
        try:self.assertEqual(core.personality()['version'],1)
        finally:core.close()
        self.assertEqual(self.http.calls,[])
    def test_non_owner_scope_cannot_review_or_reject(self):
        for action in ('list_personality','reject_personality'):
            with self.assertRaises(Fault):self.w.growth_action({'action':action,'scope':'friend','peer':'999','id':self.proposal['id'],'confirmation':'放弃成长提案'})
        self.assertEqual(len(self.w.growth_action({'action':'list_personality'})['proposals']),1)
    def test_pending_proposals_paginate_without_omission_or_mutation(self):
        with self.w.growth._db() as db:
            for n in range(24):
                db.execute("INSERT INTO growth_proposals(base_version,data,instruction,source,state,created) VALUES (1,?,?,?,'pending',?)",(json.dumps(INITIAL,ensure_ascii=False),'模拟成长','测试','2026-11-03T10:00:00+00:00'))
        first=self.w.growth_action({'action':'list_personality'})
        self.assertEqual(len(first['proposals']),20);self.assertTrue(first['has_more'])
        second=self.w.growth_action({'action':'list_personality','before_id':first['next_before_id']})
        self.assertEqual(len(second['proposals']),5);self.assertFalse(second['has_more']);self.assertIsNone(second['next_before_id'])
        ids=[r['id'] for r in first['proposals']+second['proposals']]
        self.assertEqual(len(set(ids)),25)
        with self.assertRaises(Fault):self.w.growth_action({'action':'list_personality','before_id':True})
        self.assertEqual(self.w.character_overview()['pending_proposals'],25);self.assertEqual(self.http.calls,[])
    def test_http_review_requires_session(self):
        server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port);body=json.dumps({'action':'list_personality'}).encode()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            req=urllib.request.Request(base+'/api/growth',data=body,headers={'Content-Type':'application/json'},method='POST')
            with self.assertRaises(urllib.error.HTTPError) as err:opener.open(req,timeout=5)
            self.assertEqual(err.exception.code,403)
            req=urllib.request.Request(base+'/api/growth',data=body,headers={'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base},method='POST')
            with opener.open(req,timeout=5) as response:result=json.load(response)
            self.assertEqual(result['proposals'][0]['id'],self.proposal['id']);self.assertEqual(self.http.calls,[])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
