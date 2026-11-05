"""Read-only local CosyVoice 3 file check; never imports or runs the model."""
import argparse
import json
from pathlib import Path

REQUIRED_MODEL_FILES = (
    'cosyvoice3.yaml', 'campplus.onnx', 'speech_tokenizer_v3.onnx',
    'llm.pt', 'flow.pt', 'hift.pt',
)

def inspect_local(python_executable, source_dir, model_dir):
    python=Path(python_executable)
    source=Path(source_dir)
    model=Path(model_dir)
    checks={
        'python_executable':python.is_file(),
        'cosyvoice_source':(source/'cosyvoice'/'cli'/'cosyvoice.py').is_file(),
        'model_directory':model.is_dir(),
        'tokenizer_directory':(model/'CosyVoice-BlankEN').is_dir(),
    }
    missing=[name for name in REQUIRED_MODEL_FILES if not (model/name).is_file()]
    return {
        'checks':checks,
        'missing_model_files':missing,
        'required_files_present':all(checks.values()) and not missing,
        'runtime_verified':False,
        'voice_quality_verified':False,
        'network_called':False,
        'model_started':False,
        'note':'仅检查路径存在性，未验证文件完整性、依赖、语音素材或实际合成；不会下载模型。',
    }

def main():
    parser=argparse.ArgumentParser(description='只读检查本地 CosyVoice 3 文件，不启动模型或联网')
    parser.add_argument('--python',required=True,dest='python_executable')
    parser.add_argument('--source',required=True,dest='source_dir')
    parser.add_argument('--model',required=True,dest='model_dir')
    args=parser.parse_args()
    print(json.dumps(inspect_local(args.python_executable,args.source_dir,args.model_dir),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
