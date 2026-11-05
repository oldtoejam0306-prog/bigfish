"""Reviewed UPOS upload workflow. No automatic retries or execution at import.
Protocol shape checked against maintainer sources listed in BILI-WORKBENCH.md.
Implementation is independent; authenticated platform compatibility unverified.
"""
from contextlib import nullcontext
import hashlib
import json
import math
import os
import re
import time
import urllib.parse
from pathlib import Path
from .runtime import Fault,canonical

def hash_stream(stream):
    h=hashlib.sha256()
    while True:
        chunk=stream.read(1048576)
        if not chunk:break
        h.update(chunk)
    return h.hexdigest()

def inspect_video(filename):
    p=Path(filename).expanduser().resolve()
    if not p.is_file() or p.suffix.lower() not in ('.mp4','.mkv','.webm','.mov') or not 0<p.stat().st_size<=2_000_000_000:raise Fault('视频文件无效或超过2GB')
    with p.open('rb') as f:
        value=hash_stream(f)
    return {'file':str(p),'file_bytes':p.stat().st_size,'file_sha256':value}

def valid_upload_target(pre):
    endpoint=pre.get('endpoint','')
    if endpoint.startswith('//'):endpoint='https:'+endpoint
    u=urllib.parse.urlsplit(endpoint)
    if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.bilivideo.com') or u.username or u.password or u.query or u.fragment or u.path not in ('','/') or u.port not in (None,443):raise Fault('上传CDN不在允许列表')
    uri=pre.get('upos_uri','')
    if not isinstance(uri,str) or not re.fullmatch(r'upos://[A-Za-z0-9_./-]+',uri) or '..' in uri.split('/'):raise Fault('上传对象路径无效')
    auth=pre.get('auth')
    if not isinstance(auth,str) or not auth or len(auth)>16000 or any(ord(c)<32 for c in auth):raise Fault('上传授权无效')
    return endpoint.rstrip('/')+'/'+uri.removeprefix('upos://')

class Uploader:
    def __init__(self,bili,cancel=None,progress=None):
        self.bili=bili;self.http=bili.http;self.cancel=cancel or (lambda:False);self.progress=progress or (lambda phase:None)
    def check(self):
        if self.cancel():raise Fault('上传已取消；已发出的动作不可撤回，禁止自动重试')
    def run(self,payload):
        with Path(payload['file']).open('rb') as stream:
            if hash_stream(stream)!=payload['file_sha256']:raise Fault('视频已改变')
            stream.seek(0)
            return self._run(payload,stream)
    def _run(self,payload,stream):
        """Caller must durably consume reviewed draft before entering this method."""
        expected={k:payload[k] for k in ('file','file_bytes','file_sha256')}
        if inspect_video(payload['file'])!=expected:raise Fault('草稿视频已改变，请重新审核')
        path=Path(payload['file']);self.check();self.progress('preupload')
        query=urllib.parse.urlencode({'name':path.name,'size':payload['file_bytes'],'r':'upos','profile':'ugcupos/bup','ssl':1})
        self.bili.s.gate('bili',1)
        pre=self.http.json('GET','https://member.bilibili.com/preupload?'+query,self.bili.headers())
        if not isinstance(pre,dict) or type(pre.get('OK')) is not int or pre['OK']!=1:raise Fault('预上传失败')
        target=valid_upload_target(pre);chunk=pre.get('chunk_size')
        if type(chunk) is not int or not 1_048_576<=chunk<=16_777_216:raise Fault('上传分块大小无效')
        if type(pre.get('biz_id')) is not int or pre['biz_id']<=0:raise Fault('上传业务ID无效')
        approved_chunks=[];approved_hash=hashlib.sha256()
        while True:
            block=stream.read(chunk)
            if not block:break
            approved_chunks.append(hashlib.sha256(block).digest());approved_hash.update(block)
        if approved_hash.hexdigest()!=payload['file_sha256']:raise Fault('视频已改变')
        stream.seek(0)
        headers={'X-Upos-Auth':pre['auth'],'Content-Type':'application/octet-stream'}
        self.check();self.progress('create_upload')
        init=self.http.json('POST',target+'?'+urllib.parse.urlencode({'uploads':'','output':'json','profile':'ugcupos/bup','filesize':payload['file_bytes'],'partsize':chunk,'biz_id':pre['biz_id']}),headers)
        upload_id=init.get('upload_id')
        if type(init.get('OK')) is not int or init['OK']!=1 or not isinstance(upload_id,str) or not re.fullmatch('[A-Za-z0-9-]{1,128}',upload_id):raise Fault('上传会话无效')
        parts=[];sent=0;hasher=hashlib.sha256();count=math.ceil(payload['file_bytes']/chunk)
        with nullcontext(stream) as f:
            if os.fstat(f.fileno()).st_size!=payload['file_bytes']:raise Fault('视频已改变')
            for index in range(count):
                self.check()
                if index:time.sleep(0.5)
                self.check();blob=f.read(chunk)
                if not blob or hashlib.sha256(blob).digest()!=approved_chunks[index]:raise Fault('视频分块发生改变，停止上传')
                self.progress('chunk:'+str(index+1)+'/'+str(count))
                q={'partNumber':index+1,'uploadId':upload_id,'chunk':index,'chunks':count,'size':len(blob),'start':sent,'end':sent+len(blob),'total':payload['file_bytes']}
                response,_=self.http.request('PUT',target+'?'+urllib.parse.urlencode(q),headers,blob,limit=10000)
                if response.strip()!=b'MULTIPART_PUT_SUCCESS':raise Fault('分片回执未确认')
                hasher.update(blob);sent+=len(blob);parts.append({'partNumber':index+1,'eTag':'etag'})
            if f.read(1) or sent!=payload['file_bytes'] or hasher.hexdigest()!=payload['file_sha256']:raise Fault('上传中视频发生改变，禁止完成/投稿')
        self.check();self.progress('complete_upload')
        done=self.http.json('POST',target+'?'+urllib.parse.urlencode({'output':'json','name':path.name,'profile':'ugcupos/bup','uploadId':upload_id,'biz_id':pre['biz_id']}),{'X-Upos-Auth':pre['auth']},{'parts':parts})
        if not isinstance(done,dict) or type(done.get('OK')) is not int or done['OK']!=1:raise Fault('上传完成回执未确认')
        self.check();self.progress('submit_archive')
        csrf=self.bili.s.vault.read().get('csrf')
        filename=pre['upos_uri'].split('/')[-1].rsplit('.',1)[0]
        archive={'copyright':payload['copyright'],'source':payload.get('source',''),'title':payload['title'],'tid':payload['tid'],'tag':payload['tags'],'desc':payload['text'],'dynamic':'','cover':'','no_reprint':1,'videos':[{'filename':filename,'title':payload['title'],'desc':''}],'open_elec':0,'desc_format_id':0}
        # Exactly one submission. A timeout is unknown, not a reason to send again.
        result=self.http.json('POST','https://member.bilibili.com/x/vu/web/add?'+urllib.parse.urlencode({'csrf':csrf}),self.bili.headers(),archive)
        if not isinstance(result,dict) or type(result.get('code')) is not int or result['code']!=0:raise Fault('投稿未成功，需要在独立账号创作中心核对；不要重复投稿')
        data=result.get('data')
        if not isinstance(data,dict) or not isinstance(data.get('bvid'),str) or not re.fullmatch('BV[0-9a-zA-Z]{10}',data['bvid']) or type(data.get('aid')) is not int or not 0<data['aid']<2**63:raise Fault('投稿回执不完整，结果未知')
        return {'code':0,'receipt':{'bvid':data['bvid'],'aid':data['aid']},'delivery_confirmed':False,'note':'提交接口回执，不代表审核通过/公开发布'}
