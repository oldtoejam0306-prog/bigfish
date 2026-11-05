"""Thread-safe host for an explicitly injected local duplex wire.

No device or provider is created here. HTTP authentication and user consent
are responsibilities of the caller; a timeout is an unknown outcome, not a
license to retry. Call close only after the owning workbench has stopped it.
"""
import asyncio
import concurrent.futures
import threading
from .runtime import Fault

class ThreadedDuplexTransport:
    def __init__(self,wire,*,timeout=3):
        if not isinstance(timeout,(float,int)) or isinstance(timeout,bool) or not 0<timeout<=30:raise Fault('语音传输超时设置无效')
        self.wire=wire;self.timeout=float(timeout);self.loop=asyncio.new_event_loop();self._closed=False;self._closing=False;self._close_future=None;self._inflight=set();self._guard=threading.Lock()
        self.thread=threading.Thread(target=self.loop.run_forever,daemon=True,name='bigfish-duplex-transport');self.thread.start()

    def dispatch(self,operation,payload):
        with self._guard:
            if self._closed or self._closing:raise Fault('语音传输正在关闭或已关闭')
            future=asyncio.run_coroutine_threadsafe(self.wire.dispatch(operation,payload),self.loop)
            self._inflight.add(future)
        def forget(item):
            with self._guard:self._inflight.discard(item)
        future.add_done_callback(forget)
        try:return future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:
            # Keep the underlying operation intact: its outcome is unknown.
            # No microphone is started by this transport itself.
            raise Fault('语音消息结果不明，请停止本地传输并检查会话状态') from None

    def close(self):
        with self._guard:
            if self._closed:
                if self.thread.is_alive():return False
                if not self.loop.is_closed():self.loop.close()
                return True
            if self._close_future is None:
                if any(not item.done() for item in self._inflight):return False
                self._closing=True
                async def stop_if_started():
                    bridge=self.wire.bridge
                    if bridge.id is None:return True
                    return (await bridge.stop(bridge.id)).get('state')=='stopped'
                self._close_future=asyncio.run_coroutine_threadsafe(stop_if_started(),self.loop)
            future=self._close_future
        try:stopped=future.result(timeout=self.timeout)
        except concurrent.futures.TimeoutError:return False
        except Exception:
            with self._guard:self._closing=False;self._close_future=None
            return False
        if not stopped:
            with self._guard:self._closing=False;self._close_future=None
            return False
        with self._guard:
            if self._closed:return True
            self._closed=True;self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=self.timeout)
        if self.thread.is_alive():return False
        self.loop.close();return True
