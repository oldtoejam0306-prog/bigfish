"""Explicit zero-model-cost native PCM probe; no playback or microphone.

Do not run to bypass a refused script. Review the bundled native host and obtain
the normal execution permission first. This file never changes system policy.
"""
import argparse
import asyncio
import json
import time
from bili_workbench.runtime import Fault


async def check():
    from bili_workbench.native_voice_stream import stream_pcm
    start=time.monotonic();first=None;frames=0;size=0
    source=stream_pcm('你好，这是大肥鱼的本机语音检查。',True)
    try:
        async for frame in source:
            if first is None:first=time.monotonic()-start
            frames+=1;size+=len(frame)
    finally:await source.aclose()
    return {'ok':True,'expected_format':'PCM16 mono 16000Hz','frames':frames,'bytes':size,'first_frame_ms':round(first*1000,1) if first is not None else None,'elapsed_ms':round((time.monotonic()-start)*1000,1),'played':False,'microphone':False,'model_called':False,'application_audio_file_created':False,'streaming_behavior_proven':False}


def main():
    parser=argparse.ArgumentParser(description='Check the reviewed local PCM host without microphone, playback or model calls.')
    parser.add_argument('--confirm-local-pcm',action='store_true',help='explicitly authorize the reviewed local speech process for this probe')
    args=parser.parse_args()
    if not args.confirm_local_pcm:parser.error('review the host and explicitly confirm the local-only probe first')
    try:result=asyncio.run(check())
    except Fault as error:print(json.dumps({'ok':False,'error':str(error),'automatic_retry':False}));return 1
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
