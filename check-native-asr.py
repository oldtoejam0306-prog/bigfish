"""Explicit silence-only host check. No microphone, playback or model call.

Obtain normal permission for the reviewed PS/C# host before running. This does
not modify policy, download a recognizer, log recognized text or retry failures.
"""
import argparse
import asyncio
import json
from bili_workbench.runtime import Fault


async def check():
    from bili_workbench.native_recognizer import NativeContinuousRecognizer
    from bili_workbench.duplex_session import FinalTranscript
    async def silence():
        for index in range(10):yield b'\0\0'*1600
    recognizer=NativeContinuousRecognizer(confirmation=True);unexpected=0
    async for event in recognizer.events(silence()):
        if isinstance(event,FinalTranscript):unexpected+=1
    protocol=recognizer.protocol
    return {'ok':bool(protocol and protocol.completed and unexpected==0),'input':'one_second_synthetic_silence','events':protocol.seq if protocol else 0,'unexpected_final_results':unexpected,'microphone':False,'played':False,'model_called':False,'recognition_accuracy_proven':False,'real_time_behavior_proven':False}


def main():
    parser=argparse.ArgumentParser(description='Check the reviewed continuous speech host with generated silence only.')
    parser.add_argument('--confirm-local-asr',action='store_true',help='explicitly authorize the reviewed local-only PS/C# host')
    args=parser.parse_args()
    if not args.confirm_local_asr:parser.error('review the host and explicitly confirm this local-only probe first')
    try:result=asyncio.run(check())
    except Fault as error:print(json.dumps({'ok':False,'error':str(error),'automatic_retry':False}));return 1
    print(json.dumps(result));return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
