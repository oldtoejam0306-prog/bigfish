"""Authenticated local stop; does not terminate other processes."""
import argparse
import json
import urllib.request
from pathlib import Path
from .runtime import Vault,Fault,NoRedirect

def stop(root):
    data=Vault(root).read();token=data.get('web_token');port=data.get('web_port')
    if not isinstance(token,str) or type(port) is not int or not 1<=port<=65535:raise Fault('未找到当前服务会话，请在原窗口按Ctrl+C或先核对进程')
    request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/api/stop',data=b'{}',headers={'Content-Type':'application/json','X-Local-Token':token},method='POST')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=35) as response:return json.load(response)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);args=p.parse_args()
    try:print(stop(Path(args.data_dir)))
    except Exception:raise SystemExit('停止未确认；请检查原窗口。不强制结束任何其他服务。')
if __name__=='__main__':main()
