"""下载官方 Monibuca v6 原生包；Windows x64 / Linux x64、arm64，无 Docker。

python deploy/install_monibuca.py [--dest data/monibuca] [--sha256 可信摘要]
失败立即退出，不静默降级。manifest 摘要仅记录本次下载的完整性。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def package_name(system: str, machine: str) -> str:
    arch = {"amd64": "amd64", "x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine.lower())
    osname = {"Windows": "windows", "Linux": "linux"}.get(system)
    if not arch or not osname or (osname == "windows" and arch != "amd64"):
        raise ValueError(f"官网暂无支持的预编译包：{system}/{machine}")
    return f"monibuca_v6_{osname}_{arch}." + ("zip" if osname == "windows" else "tar.gz")


def extract(archive: Path, dest: Path) -> Path:
    # 仅提取单二进制，不展开归档任意路径。
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            names = [n for n in z.namelist() if Path(n).name.lower() == "monibuca.exe"]
            if len(names) != 1:
                raise ValueError("归档中未找到唯一的 monibuca.exe")
            blob, name = z.read(names[0]), "monibuca.exe"
    else:
        with tarfile.open(archive) as t:
            items = [m for m in t.getmembers() if m.isfile() and Path(m.name).name.startswith("monibuca_linux_")]
            if len(items) != 1:
                raise ValueError("归档中未找到唯一的 Linux 二进制")
            blob, name = t.extractfile(items[0]).read(), "monibuca"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    target.write_bytes(blob)
    if name == "monibuca":
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dest", type=Path, default=Path(__file__).resolve().parents[1] / "data/monibuca")
    p.add_argument("--sha256", help="从可信渠道取得的归档摘要；未提供时仅记录下载摘要")
    args = p.parse_args()
    try:
        name = package_name(platform.system(), platform.machine())
        url = "https://download.m7s.live/bin/" + name
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / name
            with urllib.request.urlopen(url, timeout=30) as r, archive.open("wb") as out:
                shutil.copyfileobj(r, out)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if args.sha256 and digest.lower() != args.sha256.lower():
                raise ValueError("SHA256 不匹配，未安装")
            binary = extract(archive, args.dest)
        config = args.dest / "config.yml"
        if not config.exists():
            shutil.copyfile(Path(__file__).parent / "monibuca/config.yml", config)
        (args.dest / "install-manifest.json").write_text(json.dumps(
            {"version": "v6", "source": url, "sha256": digest, "binary": binary.name}, indent=2), encoding="utf-8")
        print(f"已安装：{binary.resolve()}\n启动：{binary.name} -c config.yml（工作目录 {args.dest.resolve()}）")
        return 0
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as e:
        print(f"未安装：{e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
