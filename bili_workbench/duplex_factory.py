"""Explicit opt-in assembly for the existing guarded local voice components.

No device is opened and no model is called when constructing the transport.
The caller must expose it only behind authenticated same-origin endpoints,
obtain browser microphone permission and run/stop it within workbench lifetime.
"""
from .runtime import Fault
from .duplex_adapters import EnergySegmentRecognizer,WindowsBufferedTranscriber,LocalChatModelAdapter,WindowsSegmentedSynthesizer,WindowsPCMStreamSynthesizer,QueueSpeaker
from .duplex_bridge import DuplexBridge
from .duplex_session import DuplexSession
from .duplex_transport import ThreadedDuplexTransport
from .duplex_wire import DuplexWire


def build_local_duplex_transport(workbench,*,speech_confirmation,model_confirmation,echo_mode,include_memory=False,include_knowledge=False,context_confirmation='',readiness_probe=None,speech_backend='buffered',recognition_backend='segmented'):
    if speech_confirmation!='确认使用本机语音识别和合成':raise Fault('请明确确认本机语音处理')
    if model_confirmation!='确认语音会话按预算自动调用模型':raise Fault('请明确确认语音会话的自动模型调用')
    if type(include_memory) is not bool or type(include_knowledge) is not bool:raise Fault('语音上下文选项无效')
    if (include_memory or include_knowledge) and context_confirmation!='确认将所选记忆或知识发送模型':raise Fault('请明确确认所选上下文将发送给模型')
    if echo_mode not in ('headphones','browser-echo-cancellation'):raise Fault('请选择回声控制方式')
    if speech_backend not in ('buffered','windows-pcm-stream'):raise Fault('语音合成后端无效')
    if recognition_backend not in ('segmented','windows-continuous'):raise Fault('语音识别后端无效')
    if workbench.cancel_event.is_set():raise Fault('工作台已暂停，不能开始语音会话')
    with workbench.s.lock:config=workbench.s.config()
    if not config['paid_enabled'] or not config['daily_budget_micro'] or not config['request_cap_micro']:raise Fault('模型付费未启用或预算为零')
    if not config['provider_url'] or not config['model'] or not config['price_version'] or not config['input_price_micro_per_million'] or not config['output_price_micro_per_million']:raise Fault('模型地址、型号或价格未完成配置')
    if readiness_probe is None:
        from .voice_input import readiness
        readiness_probe=readiness
    status=readiness_probe()
    if not isinstance(status,dict) or status.get('ready') is not True:raise Fault('本机语音运行条件未满足；没有启动识别脚本')
    speaker=QueueSpeaker();recognizer=EnergySegmentRecognizer(WindowsBufferedTranscriber())
    if recognition_backend=='windows-continuous':
        from .native_recognizer import NativeContinuousRecognizer
        recognizer=NativeContinuousRecognizer(confirmation=True)
    from .model_stream import supported
    model=LocalChatModelAdapter(workbench.local_chat,include_memory=include_memory,include_knowledge=include_knowledge,stream_model=supported(config['provider_url'],config['model']))
    synthesizer=WindowsPCMStreamSynthesizer() if speech_backend=='windows-pcm-stream' else WindowsSegmentedSynthesizer()
    session=DuplexSession(recognizer,model,synthesizer,speaker,authorize_turn=lambda:True,echo_mode=echo_mode)
    return ThreadedDuplexTransport(DuplexWire(DuplexBridge(session,speaker)))
