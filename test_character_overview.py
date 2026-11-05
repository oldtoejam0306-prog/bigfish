import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from core import Core
from bili_workbench.service import Workbench
from bili_workbench.web import Server
from test_workbench import FakeHTTP

class CharacterOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.http=FakeHTTP();self.w=Workbench(self.tmp.name,self.http)
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def test_empty_state_does_not_create_character_or_call_services(self):
        result=self.w.character_overview()
        self.assertIsNone(result['personality']);self.assertEqual(result['interests'],[])
        self.assertEqual(result['recent_activity'],[])
        self.assertEqual(result['pending_proposals'],0);self.assertFalse(result['model_called'])
        self.assertFalse((Path(self.tmp.name)/'character.db').exists());self.assertEqual(self.http.calls,[])
    def test_local_personality_and_public_interest_only(self):
        core=Core(Path(self.tmp.name)/'character.db');personality=core.personality();core.close()
        self.w.interests.correct('绘画',0.5,'用户明确设置公开兴趣')
        result=self.w.character_overview()
        self.assertEqual(result['personality']['version'],personality['version'])
        self.assertEqual(result['interests'][0]['topic'],'绘画')
        self.assertNotIn('evidence',result['interests'][0]);self.assertNotIn('memories',result)
        self.assertEqual(self.http.calls,[])
    def test_activity_shows_coverage_without_private_content_or_other_account(self):
        self.w.s.configure({'account_mid':'123'})
        self.w.interests.record_observation('BV1q2nW6DEaf','metadata','fixture',now='2026-11-03T10:00:00+00:00')
        self.w.s.db.execute('INSERT INTO public_video_observations VALUES (?,?,?,?,?,?)',('456','BV0000000001','subtitle','fixture','other-account-secret','2026-11-03T11:00:00+00:00'))
        self.w.s.db.execute('INSERT INTO qq_native_inbox VALUES (?,?,?,?,?)',('private-identifier','private-message-body','pending','private-response',1793703600))
        with self.w.growth._db() as db:db.execute("INSERT INTO growth_proposals(base_version,data,instruction,source,state,created) VALUES (1,'private-growth','private-instruction','private-source','pending','2026-11-03T10:30:00+00:00')")
        result=self.w.character_overview();text=json.dumps(result,ensure_ascii=False)
        self.assertEqual({x['kind'] for x in result['recent_activity']},{'bili','qq','growth'})
        self.assertIn('仅元数据',text);self.assertIn('BV1q2nW6DEaf',text)
        for private in ('private-identifier','private-message-body','private-response','private-growth','private-instruction','other-account-secret','BV0000000001'):
            self.assertNotIn(private,text)
        self.assertFalse(result['model_called']);self.assertFalse(result['platform_called']);self.assertEqual(self.http.calls,[])
    def test_overview_requires_session_authorization(self):
        server=Server(('127.0.0.1',0),self.w);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)+'/api/character-overview';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:opener.open(url,timeout=5)
            self.assertEqual(error.exception.code,403)
            req=urllib.request.Request(url,headers={'X-Local-Token':server.token})
            with opener.open(req,timeout=5) as response: result=json.load(response)
            self.assertIsNone(result['personality']);self.assertFalse(result['platform_called'])
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
