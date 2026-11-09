"""从发布 ZIP 生成升级清单(version/archive/sha256/size/notes)。

用法:
    python deploy/make_release_manifest.py --zip dist/stepeye-1.1.0.zip --version 1.1.0 \
        --out dist/stepeye-1.1.0.manifest.json --notes "..."

清单只描述离线包,不下载、不上传、不签名。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path, help="发布 ZIP")
    parser.add_argument("--version", required=True, help="版本号,如 1.1.0")
    parser.add_argument("--out", type=Path, help="清单输出路径(缺省与 ZIP 同名 .manifest.json)")
    parser.add_argument("--notes", default="", help="发布说明")
    args = parser.parse_args()
    archive = args.zip.resolve()
    if not archive.is_file():
        raise SystemExit(f"ZIP 不存在：{archive}")
    version = args.version.strip().lstrip("vV")
    if not version or any(not part.isdigit() for part in version.split("-", 1)[0].split(".")):
        raise SystemExit(f"版本号无效：{args.version}")
    output = args.out or archive.with_suffix(".manifest.json")
    payload = {
        "version": version,
        "archive": archive.name,
        "sha256": sha256_file(archive),
        "size": archive.stat().st_size,
        "notes": args.notes,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
