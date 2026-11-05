"""Start a user-visible, read-only workbench with a fresh temporary data dir."""
import sys
import tempfile
from . import web

def main():
    root=tempfile.mkdtemp(prefix='bigfish-ui-preview-')
    print('只读预览使用新建临时目录，不读取现有账号或数据。请勿输入Key。',flush=True)
    print('预览数据目录：'+root,flush=True)
    previous=sys.argv
    try:
        sys.argv=['bigfish-preview','--data-dir',root,'--port','0','--preview']
        web.main()
    finally:sys.argv=previous

if __name__=='__main__':main()
