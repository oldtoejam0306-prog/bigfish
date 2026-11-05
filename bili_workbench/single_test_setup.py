"""Create a fresh, no-credential directory for the one authorized test."""
import argparse
from pathlib import Path
from .service import Workbench
from .deepseek_test import prepare

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);args=parser.parse_args()
    root=Path(args.data_dir)
    root.mkdir(parents=True,exist_ok=False)
    w=Workbench(root)
    try:
        prepare(w)
        w.s.configure({'daily_budget_micro':1000000,'request_cap_micro':100000,'paid_enabled':True,'outbound_enabled':False,'qq_auto_process':False,'qq_auto_send':False,'qq_sender_enabled':False})
        print('单次测试目录已准备；没有保存API Key，也没有调用网络。')
    finally:w.close()

if __name__=='__main__':main()
