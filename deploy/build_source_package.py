"""打包可启动的源码包:代码、前端、配置、文档、演示模型与启动脚本。

不包含训练/评测用的大模型、dist、.v0、缓存与测试视频。解压后双击
START-WINDOWS.bat(或运行 START-LINUX.sh)即可创建虚拟环境并启动演示工位。

用法:
    python deploy/build_source_package.py --out ../.v0/release/stepeye-source-0.9.0.zip --version 0.9.0
"""
from __future__ import annotations

import argparse
import hashlib
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".git", ".github", ".v0", "dist", "__pycache__", ".pytest_cache", "node_modules",
                ".venv", "venv", "output", "test-videos", ".workbuddy-ai", "data", "build"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip", ".7z", ".exe", ".dll", ".pyd"}
# 源码包只带入口文件与代码目录;模型目录单独按白名单收。
TOP_FILES = ("README.md", ".env.example", "pytest.ini")
TOP_DIRS = ("visual", "web", "configs", "deploy", "tools", "tests", "tests-js", "docs")
# models/ 下只带演示所需模型与所有 JSON 注册表;训练/评测权重不进源码包。
DEMO_MODELS = {"hand_landmarker.task", "pose_landmarker_lite.task", "efficientdet_lite0.tflite"}
MODEL_SKIP_DIRS = {"community", "action", "mechanism"}

START_HERE = """StepEye {version} 源码包

Windows:
  1. 安装 Python 3.11+（勾选 Add to PATH）
  2. 双击 START-WINDOWS.bat
  3. 浏览器打开 http://127.0.0.1:8000

Linux/macOS:
  sh START-LINUX.sh
  浏览器打开 http://127.0.0.1:8000

演示工位为仿真画面，不需要摄像头。启用登录：
  python -m visual passwd admin --role admin --data data
  python -m visual serve configs/demo_sim.yaml --port 8000 --auth

完整说明见 README.md 与 docs/USAGE.md。
"""


def keep_model(path: Path) -> bool:
    rel = path.relative_to(ROOT / "models")
    if rel.parts and rel.parts[0] in MODEL_SKIP_DIRS:
        return False
    if path.suffix.lower() == ".onnx":
        return False
    if len(rel.parts) == 1:
        return path.name in DEMO_MODELS
    return path.suffix.lower() in {".json", ".md", ".txt"} or path.name.startswith("LICENSE")


def add_tree(bundle: zipfile.ZipFile, source: Path, arc_root: str, *, filter_models: bool = False) -> int:
    count = 0
    for current, dirs, files in os.walk(source):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for name in sorted(files):
            path = current_path / name
            if path.suffix.lower() in EXCLUDE_SUFFIX:
                continue
            if filter_models and not keep_model(path):
                continue
            rel = path.relative_to(source)
            bundle.write(path, f"{arc_root}/{rel.as_posix()}")
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", default="0.9.0")
    args = parser.parse_args()
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    arc_root = f"StepEye-{args.version}"
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in TOP_FILES:
            path = ROOT / name
            if path.is_file():
                bundle.write(path, f"{arc_root}/{name}")
                count += 1
        for name in sorted(ROOT.glob("requirements*.txt")):
            bundle.write(name, f"{arc_root}/{name.name}")
            count += 1
        for name in TOP_DIRS:
            path = ROOT / name
            if path.is_dir():
                count += add_tree(bundle, path, f"{arc_root}/{name}")
        count += add_tree(bundle, ROOT / "models", f"{arc_root}/models", filter_models=True)
        for name in ("START-WINDOWS.bat", "START-LINUX.sh"):
            path = ROOT / "deploy" / "source" / name
            if path.is_file():
                bundle.write(path, f"{arc_root}/{name}")
                count += 1
        bundle.writestr(f"{arc_root}/START-HERE.txt", START_HERE.format(version=args.version))
        count += 1
    print(f"files={count} bytes={output.stat().st_size} sha256={sha256_file(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
