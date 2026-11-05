"""Strict JSON-safe messages for an authenticated local duplex transport.

No HTTP listener or browser capture is created here. The eventual transport
must enforce same-origin session authentication before calling dispatch.
"""
import base64
import binascii
from .runtime import Fault

class DuplexWire:
    def __init__(self,bridge):self.bridge=bridge

    @staticmethod
    def _fields(value,required,optional=()):
        if not isinstance(value,dict) or not set(required).issubset(value) or set(value)-set(required)-set(optional):raise Fault('语音消息字段无效')

    async def dispatch(self,operation,payload):
        if operation=='start':
            self._fields(payload,('confirmation',),('headphones_confirmed','browser_echo_reported'))
            return await self.bridge.start(payload['confirmation'],headphones_confirmed=payload.get('headphones_confirmed',False),browser_echo_reported=payload.get('browser_echo_reported',False))
        if operation=='feed':
            self._fields(payload,('id','sequence','pcm_base64'))
            encoded=payload['pcm_base64']
            if not isinstance(encoded,str) or not 0<len(encoded)<=4268:raise Fault('音频编码无效')
            try:pcm=base64.b64decode(encoded,validate=True)
            except (binascii.Error,ValueError):raise Fault('音频编码无效') from None
            if not 0<len(pcm)<=3200 or len(pcm)%2:raise Fault('音频帧格式无效')
            return await self.bridge.feed(payload['id'],payload['sequence'],pcm)
        if operation=='poll':
            self._fields(payload,('id','ack'),('status_only',))
            result=await self.bridge.poll(payload['id'],payload['ack'],status_only=payload.get('status_only',False));output=result['output']
            if output is not None:
                result=dict(result,output={'sequence':output['sequence'],'pcm_base64':base64.b64encode(output['pcm']).decode('ascii')})
            return result
        if operation=='stop':
            self._fields(payload,('id',))
            return await self.bridge.stop(payload['id'])
        raise Fault('未知语音消息')
