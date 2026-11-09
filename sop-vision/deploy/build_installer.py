"""把便携运行目录打成 Windows 自解压安装包(7-Zip SFX)。

产物是一个 exe:运行后解压到临时目录并执行 install.bat,安装到
%LOCALAPPDATA%\\StepEye(可用 STEPEYE_TARGET 覆盖),创建开始菜单快捷方式。

用法:
    python deploy/build_installer.py --runtime dist/stepeye-runtime --version 0.9.0 \
        --out ../.v0/release/StepEye-Setup-0.9.0.exe
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_7Z = Path(r"C:\Program Files\7-Zip\7z.exe")
DEFAULT_SFX = Path(r"C:\Program Files\7-Zip\7z.sfx")

CONFIG = """;!@Install@!UTF-8!
Title="StepEye {version}"
BeginPrompt="安装 StepEye {version} 到本机？"
Progress="yes"
RunProgram="install.bat"
;!@InstallEnd@!
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="dist/stepeye-runtime 目录")
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sevenzip", type=Path, default=DEFAULT_7Z)
    parser.add_argument("--sfx", type=Path, default=DEFAULT_SFX)
    parser.add_argument("--keep-staging", action="store_true")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if not (runtime / "stepeye-runtime.exe").is_file():
        raise SystemExit(f"运行目录缺少 stepeye-runtime.exe: {runtime}")
    if not args.sevenzip.is_file() or not args.sfx.is_file():
        raise SystemExit("未找到 7z.exe / 7z.sfx，请安装 7-Zip 或指定路径")
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / "installer-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"复制运行目录到暂存区: {runtime} -> {staging / 'stepeye-runtime'}")
    shutil.copytree(runtime, staging / "stepeye-runtime")
    installer_dir = ROOT / "deploy" / "installer"
    install_bat = (installer_dir / "install.bat").read_text(encoding="utf-8")
    (staging / "install.bat").write_text(
        install_bat.replace("%STEPEYE_VERSION%", args.version), encoding="utf-8")
    shutil.copy2(installer_dir / "README.txt", staging / "README.txt")

    config = output.parent / "installer-config.txt"
    config.write_text(CONFIG.format(version=args.version), encoding="utf-8")
    payload = output.parent / "installer-payload.7z"
    if payload.exists():
        payload.unlink()
    print("压缩安装负载（约 1GB，需几分钟）...")
    command = [str(args.sevenzip), "a", "-t7z", "-mx=5", "-mmt=on", str(payload), "."]
    completed = subprocess.run(command, cwd=staging)
    if completed.returncode:
        raise SystemExit(f"7z 压缩失败，退出码 {completed.returncode}")
    print(f"拼接 SFX: {args.sfx.name} + config + payload")
    with open(output, "wb") as target:
        for part in (args.sfx, config, payload):
            with open(part, "rb") as source:
                shutil.copyfileobj(source, target)
    print(f"installer={output} bytes={output.stat().st_size} sha256={sha256_file(output)}")
    if not args.keep_staging:
        shutil.rmtree(staging)
        payload.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
