"""Owner-provided, bounded local PNG sticker collection."""
import base64
import binascii
import re
import secrets
import struct
import time
import zlib
from .runtime import Fault


class Stickers:
    MAX_BYTES=400000
    def __init__(self,state):
        self.s=state
        self.s.db.execute('CREATE TABLE IF NOT EXISTS local_stickers (id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,png BLOB NOT NULL,created REAL NOT NULL)')

    @staticmethod
    def validate_png(data):
        if len(data)>Stickers.MAX_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):raise Fault('仅支持不超过 400 KB 的 PNG 图片')
        pos=8;seen_header=False;seen_data=False;ended=False
        while pos+12<=len(data):
            size=struct.unpack('>I',data[pos:pos+4])[0];end=pos+12+size
            if end>len(data):raise Fault('PNG 内容不完整')
            kind=data[pos+4:pos+8];body=data[pos+8:pos+8+size];crc=struct.unpack('>I',data[pos+8+size:end])[0]
            if zlib.crc32(kind+body)&0xffffffff != crc:raise Fault('PNG 校验失败')
            if not seen_header:
                if kind!=b'IHDR' or size!=13:raise Fault('PNG 头无效')
                width,height=struct.unpack('>II',body[:8])
                if not (0<width<=2048 and 0<height<=2048 and width*height<=2000000):raise Fault('图片尺寸过大')
                seen_header=True
            elif kind==b'IHDR':raise Fault('PNG 头重复')
            if kind==b'acTL':raise Fault('暂不支持动态 PNG')
            if kind==b'IDAT':seen_data=True
            pos=end
            if kind==b'IEND':
                ended=True
                if size or pos!=len(data):raise Fault('PNG 尾部无效')
                break
        if not seen_header or not seen_data or not ended:raise Fault('PNG 内容不完整')

    def add(self,name,encoded):
        if not isinstance(name,str) or not name.strip() or len(name)>40 or any(c in name for c in '[]【】') or any(ord(c)<32 for c in name):raise Fault('表情名称无效')
        if not isinstance(encoded,str) or len(encoded)>((self.MAX_BYTES+2)//3)*4:raise Fault('图片过大或格式无效')
        try:data=base64.b64decode(encoded,validate=True)
        except (binascii.Error,ValueError):raise Fault('图片编码无效')
        self.validate_png(data);name=name.strip()
        with self.s.lock:
            if self.s.db.execute('SELECT COUNT(*) FROM local_stickers').fetchone()[0]>=50:raise Fault('本机表情数量已达上限')
            if self.s.db.execute('SELECT 1 FROM local_stickers WHERE name=?',(name,)).fetchone():raise Fault('已有同名表情，请换一个名称')
            item_id=secrets.token_hex(12)
            self.s.db.execute('INSERT INTO local_stickers VALUES (?,?,?,?)',(item_id,name,data,time.time()))
        return {'id':item_id,'name':name}

    def list(self):
        with self.s.lock:rows=self.s.db.execute('SELECT id,name FROM local_stickers ORDER BY created,id').fetchall()
        return {'items':[dict(row) for row in rows]}

    def get(self,item_id):
        self._validate_id(item_id)
        with self.s.lock:row=self.s.db.execute('SELECT id,name,png FROM local_stickers WHERE id=?',(item_id,)).fetchone()
        if not row:raise Fault('表情不存在')
        return {'id':row['id'],'name':row['name'],'png_base64':base64.b64encode(row['png']).decode('ascii')}

    def delete(self,item_id):
        self._validate_id(item_id)
        with self.s.lock:count=self.s.db.execute('DELETE FROM local_stickers WHERE id=?',(item_id,)).rowcount
        if not count:raise Fault('表情不存在')
        return {'deleted':True}

    @staticmethod
    def _validate_id(item_id):
        if not isinstance(item_id,str) or not re.fullmatch(r'[0-9a-f]{24}',item_id):raise Fault('表情编号无效')
