"""创建/核验独立训练环境；可显式只读借用本机现有GPU包，禁止修改主环境。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

PROBE = r'''
import json,sys,importlib.metadata as m,hashlib
from pathlib import Path
import numpy,cv2,torch,torchvision,ultralytics,onnx
versions={name:m.version(name) for name in ['numpy','opencv-python','torch','torchvision','ultralytics','onnx','onnxruntime','Pillow','PyYAML']}
metadata_hashes={name:{part:hashlib.sha256((m.distribution(name).read_text(part) or '').encode()).hexdigest()
 for part in ['METADATA','RECORD']} for name in versions}
print(json.dumps({'python':sys.version,'executable':sys.executable,'prefix':sys.prefix,'versions':versions,
 'distribution_metadata_sha256':metadata_hashes,
 'numpy_path':numpy.__file__,'torch_path':torch.__file__,'cuda':torch.version.cuda,'gpu_available':torch.cuda.is_available(),
 'gpu_name':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None},ensure_ascii=True))
'''


def executable(folder):
    return Path(folder)/("Scripts/python.exe" if os.name=="nt" else "bin/python")


def environment(folder: Path, *, borrow_site: Path | None=None, install=False):
    folder=Path(folder).resolve()
    if folder==Path(sys.prefix).resolve(): raise ValueError("禁止修改当前主Python环境")
    if folder.exists() and not (folder/"pyvenv.cfg").is_file(): raise ValueError("目标存在且不是虚拟环境")
    if not folder.exists(): venv.EnvBuilder(with_pip=True).create(folder)
    python=executable(folder)
    safe_env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1","PIP_DISABLE_PIP_VERSION_CHECK":"1"}
    if borrow_site is not None:
        borrow_site=Path(borrow_site).resolve()
        if not (borrow_site/"torch").is_dir(): raise ValueError("借用目录没有torch")
        target=folder/("Lib/site-packages" if os.name=="nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        target.mkdir(parents=True,exist_ok=True)
        (target/"stepeye-readonly-host.pth").write_text(str(borrow_site)+"\n",encoding="utf8")
        # Local OpenBLAS numpy precedes borrowed packages; never change borrowed MKL/DLLs.
        subprocess.run([str(python),"-m","pip","install","--no-deps","--ignore-installed","--index-url","https://pypi.org/simple","numpy==2.5.4"],
                       env=safe_env,check=True)
    if install:
        requirements=Path(__file__).resolve().parents[1]/"requirements-training.txt"
        subprocess.run([str(python),"-m","pip","install","--index-url","https://pypi.org/simple","-r",str(requirements)],
                       env=safe_env,check=True)
    return python


def inspect(python: Path, output: Path):
    env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1","YOLO_CONFIG_DIR":str(Path(output).resolve().parent/"yolo-settings"),
         "YOLO_AUTOINSTALL":"false","YOLO_OFFLINE":"true","WANDB_MODE":"disabled"}
    result=subprocess.run([str(python),"-c",PROBE],env=env,capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError("训练环境导入失败；请隔离检查依赖，不要修改主环境")
    # Ultralytics may print a first-run settings notice before this JSON line.
    lines=[line for line in result.stdout.splitlines() if line.startswith('{"python":')]
    if not lines: raise RuntimeError("训练环境未返回有效检查结果")
    report=json.loads(lines[-1]);path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv",type=Path,required=True);parser.add_argument("--borrow-site",type=Path)
    parser.add_argument("--install",action="store_true");parser.add_argument("--check-only",action="store_true")
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    python=executable(args.venv) if args.check_only else environment(args.venv,borrow_site=args.borrow_site,install=args.install)
    report=inspect(python,args.report)
    print(json.dumps(report,ensure_ascii=True));return 0


if __name__=="__main__": raise SystemExit(main())
