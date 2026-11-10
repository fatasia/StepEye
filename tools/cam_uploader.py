"""定点相机/文件夹自动上传(T10):监视目录,按文件名前缀识别场景,提交拍照即检。

用法: python tools/cam_uploader.py --server http://127.0.0.1:8000 --watch D:\\camdrop
场景映射:文件名前缀=场景 id(如 glove_check_20261103.jpg → 场景 glove_check);
定点相机定时落图到监视目录即可,本脚本轮询上传后把原图移入 done/。
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def upload(server: str, scenario: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"scenario_id\"\r\n\r\n{scenario}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(f"{server.rstrip('/')}/api/inspection/tasks", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.load(r)


def main() -> int:
    p = argparse.ArgumentParser(description="定点相机图片自动上传")
    p.add_argument("--server", default="http://127.0.0.1:8000")
    p.add_argument("--watch", required=True, help="监视目录(相机/脚本往里落图)")
    p.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒")
    p.add_argument("--default-scenario", default="", help="文件名无前缀时用的场景 id")
    args = p.parse_args()
    watch = Path(args.watch)
    done = watch / "done"
    watch.mkdir(parents=True, exist_ok=True)
    print(f"监视 {watch}(前缀=场景id,如 glove_check_xxx.jpg;无前缀用 --default-scenario)")
    while True:
        for f in sorted(watch.iterdir()):
            if not f.is_file() or f.suffix.lower() not in IMAGE_EXTS:
                continue
            scenario = args.default_scenario or f.stem.split("_")[0]
            try:
                rec = upload(args.server, scenario, f)
                print(f"[ok] {f.name} -> {rec['id']}({rec['status']})")
                done.mkdir(exist_ok=True)
                shutil.move(str(f), done / f.name)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:120]
                print(f"[失败] {f.name}: HTTP {e.code} {detail}", flush=True)
                bad = watch / "failed"
                bad.mkdir(exist_ok=True)
                shutil.move(str(f), bad / f.name)  # 失败进 failed/,不无限重试
            except Exception as err:  # noqa: BLE001
                print(f"[失败] {f.name}: {err!r}", flush=True)
                time.sleep(args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    import sys
    sys.exit(main())
