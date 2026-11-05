import hashlib
import hmac
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from bili_workbench.runtime import Fault, Vault, canonical
from bili_workbench.service import Workbench
from bili_workbench.ws_event_candidate import parse_preview
from test_workbench import FakeHTTP, BV

class IntegrationBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.vault=patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data)
        self.vault.start(); self.http=FakeHTTP(); self.w=Workbench(self.temp.name,self.http)
        self.w.s.gate=lambda *a,**k:None
    def tearDown(self):
        self.w.close();self.vault.stop();self.temp.cleanup()
    def test_ws_preview_cannot_be_promoted_to_authenticated_event(self):
        self.w.s.configure({'qq_endpoint':'http://127.0.0.1:9999'})
        self.w.qq.configure({'bot_id':10001,'owner_id':20002,'enabled':True})
        self.w.s.vault.save({'qq_access_token':'fixture-access-token-0000','qq_event_secret':'fixture-event-secret-0000'})
        event={'time':int(time.time()),'self_id':10001,'user_id':20002,'message_id':1,'post_type':'message','message_type':'private','sub_type':'friend','message':[{'type':'text','data':{'text':'/记住 fixture'}}]}
        body=canonical(event).encode();preview=parse_preview(body,10001,{20002})
        transport=self.w.qq_transport()
        with self.assertRaises(Fault):self.w.qq.process_authenticated(transport,preview['event'])
        with self.assertRaises(Fault):self.w.accept_qq_event(body,{'Content-Type':'application/json','X-Self-ID':'10001'})
        self.assertEqual(self.w.qq_inbox_status()['inbox'],[])
        self.assertEqual(self.http.calls,[])
        headers={'Content-Type':'application/json','X-Self-ID':'10001','X-Signature':'sha1='+hmac.new(b'fixture-event-secret-0000',body,hashlib.sha1).hexdigest()}
        ambiguous=dict(headers);ambiguous['x-signature']=headers['X-Signature']
        with self.assertRaises(Fault):self.w.accept_qq_event(body,ambiguous)
        self.assertEqual(self.w.qq_inbox_status()['inbox'],[])
        receipt=self.w.accept_qq_event(body,headers)
        self.assertFalse(receipt['model_called']);self.assertFalse(receipt['sent'])
        self.assertEqual(self.w.qq_inbox_status()['inbox'][0]['state'],'pending')
        self.assertEqual(self.w.process_qq_inbox()['status'],'needs_transport_validation')
        self.assertEqual(self.http.calls,[])
    def test_malformed_subtitle_metadata_never_downloads(self):
        self.w.s.configure({'read_enabled':True})
        for subtitle in ([], {'subtitles':[{'lan':'zh','subtitle_url':None}]}, {'subtitles':[{'lan':'zh','subtitle_url':'https://a.hdslb.com:bad/x'}]}):
            def get(path,*a,**kw):
                if 'view' in path:return {'bvid':BV,'aid':1,'cid':2,'title':'fixture'}
                return {'subtitle':subtitle}
            with self.subTest(subtitle=subtitle),patch.object(self.w.bili,'get',side_effect=get):
                with self.assertRaises(Fault):self.w.bili.subtitle(BV,refresh=True)
        self.assertEqual(self.http.calls,[])

if __name__=='__main__':unittest.main()
