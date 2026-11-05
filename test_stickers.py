import base64,struct,tempfile,unittest,zlib
from unittest.mock import patch
from bili_workbench.runtime import Fault,Vault
from bili_workbench.service import Workbench
from test_workbench import FakeHTTP

def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
def png(width=1,height=1):
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\xff\xff'))+chunk(b'IEND',b'')

class StickerTests(unittest.TestCase):
 def test_add_get_list_delete_and_reject_bad_png(self):
  with tempfile.TemporaryDirectory() as root, patch.object(Vault,'_crypt',lambda self,data,decrypt=False:data):
   http=FakeHTTP();w=Workbench(root,http)
   try:
    encoded=base64.b64encode(png()).decode();item=w.stickers.add('示例',encoded)
    self.assertEqual(w.stickers.get(item['id'])['png_base64'],encoded)
    self.assertEqual(w.stickers.list()['items'],[item])
    for name,data in [('重复',b'bad'),('过大像素',png(2000,2000)),('损坏',png()[:-1]),('伪造',png()+b'extra')]:
     with self.subTest(name=name),self.assertRaises(Fault):w.stickers.add(name,base64.b64encode(data).decode())
    with self.assertRaises(Fault):w.stickers.add('示例',encoded)
    self.assertTrue(w.stickers.delete(item['id'])['deleted'])
    with self.assertRaises(Fault):w.stickers.get(item['id'])
    self.assertEqual(http.calls,[])
   finally:w.close()

if __name__=='__main__':unittest.main()
