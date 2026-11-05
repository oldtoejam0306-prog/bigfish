"""Persistent local owner-only interactive prototype. No network adapter."""
import argparse
import json
from pathlib import Path
from app import BigFish
from boundary import FixtureSubtitleProvider
from vtt_provider import LocalVttProvider
from routing import plan_video
from vendor.webvtt.errors import MalformedFileError, MalformedCaptionError

def main():
    parser=argparse.ArgumentParser(description='大肥鱼离线养成原型（仅本机主人）')
    parser.add_argument('--db',required=True,help='独立 SQLite 文件路径')
    parser.add_argument('--subtitle',help='显式指定本地 WebVTT 文件（可选）')
    args=parser.parse_args(); path=Path(args.db); path.parent.mkdir(parents=True,exist_ok=True)
    provider=LocalVttProvider('local-video',Path(args.subtitle).read_text(encoding='utf-8-sig')) if args.subtitle else FixtureSubtitleProvider({}); app=BigFish(path,provider,provider.revision); session=app.local_owner_session()
    print('离线本地主人模式。命令：人格、记忆、记住 文本、纠正兴趣 内容、删除 ID、读字幕 请求ID、计划 字幕|画面|难例、退出')
    try:
        while True:
            try: line=input('> ').strip()
            except EOFError: break
            try:
                if line=='退出': break
                if line=='人格': print(json.dumps(session.personality(),ensure_ascii=False))
                elif line=='记忆': print(json.dumps(session.memories(),ensure_ascii=False))
                elif line.startswith('记住 '): print('记忆 ID',session.remember('long',line[3:].strip(),'本地主人',1.0))
                elif line.startswith('纠正兴趣 '):
                    p=session.personality()['data']; p['creative_interest']=line[5:].strip()
                    print('人格版本',session.correct('本地主人纠正创作兴趣',p))
                elif line.startswith('计划 '):
                    parts=line[3:].split()
                    if len(parts) not in (1,5): raise ValueError('用法：计划 字幕|画面|难例 [输入tokens 输出tokens 输入单价 输出单价]')
                    print(json.dumps(plan_video(parts[0],[int(x) for x in parts[1:]] if len(parts)==5 else None),ensure_ascii=False))
                elif line.startswith('读字幕 '):
                    if not args.subtitle:
                        print('未指定字幕文件；请使用 --subtitle 指定本地 WebVTT 文件。')
                        continue
                    print(json.dumps(session.read_fixture('local-video',line[4:].strip()),ensure_ascii=False))
                elif line.startswith('删除 '): session.change_memory(int(line[3:]),'本地主人要求删除'); print('已逻辑删除')
                elif line: print('未知命令')
            except (MalformedFileError, MalformedCaptionError):
                print('字幕格式无效；请求已标记为结果不确定，不会自动重试或释放预留。请检查文件并重新启动。')
            except RuntimeError as e:
                print('请求无法继续：',e,'；不会自动重试或释放预留。')
            except (ValueError,KeyError) as e: print('错误:',e)
    finally: app.close()
if __name__=='__main__': main()
