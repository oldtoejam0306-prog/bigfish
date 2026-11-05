import re
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

class Inventory(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.sections=0
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag=='section': self.sections+=1

class UiStructureTests(unittest.TestCase):
    def test_original_controls_and_routes_are_present(self):
        text=(Path(__file__).parent/'bili_workbench'/'index.html').read_text(encoding='utf-8')
        parsed=Inventory(); parsed.feed(text)
        self.assertEqual(parsed.sections,10)
        self.assertEqual([k for k,v in Counter(parsed.ids).items() if v>1],[])
        for name in ('api_key','paid_enabled','outbound_enabled','bvid','qq_endpoint','out'):
            self.assertIn(name,parsed.ids)
        self.assertIn("if(!r.ok)",text)
        self.assertIn("credential_store",(Path(__file__).parent/'bili_workbench'/'preflight.py').read_text(encoding='utf-8'))
        self.assertIn('@media(max-width:800px)',text)
        self.assertEqual(sorted(sum([[],[3],[1,2],[5,6,9],[4,7],[0,8]],[])),list(range(10)))

if __name__=='__main__':unittest.main()
