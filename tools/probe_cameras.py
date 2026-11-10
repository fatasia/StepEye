"""真实协议探针：只用部署环境变量，不输出密码、token 或带凭据的 RTSP URL。

python tools/probe_cameras.py --onvif [--wvp]
可配 ONVIF_HOST/PORT/USER/PASS 与 GB28181_WVP_URL/USER/PASS。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.llm_provider import _default_env_path, load_env_file


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--onvif", action="store_true")
    p.add_argument("--wvp", action="store_true")
    args = p.parse_args()
    env = {**load_env_file(_default_env_path()), **os.environ}
    results = {}; failed = False
    if args.onvif:
        from visual.onvif import device_rtsp_and_geo
        host = env.get("ONVIF_HOST", "")
        if not host:
            results["onvif"] = {"status": "unconfigured"}; failed = True
        else:
            try:
                r = device_rtsp_and_geo(host, int(env.get("ONVIF_PORT", "80")), env.get("ONVIF_USER", ""), env.get("ONVIF_PASS", ""))
                results["onvif"] = {"status": "ok", "has_rtsp": bool(r["rtsp"]), "has_geo": r["lat"] is not None and r["lng"] is not None}
            except Exception:
                results["onvif"] = {"status": "failed", "hint": "检查服务、地址及凭据"}; failed = True
    if args.wvp:
        from visual.gb28181 import WvpClient
        base = env.get("GB28181_WVP_URL", "")
        if not base:
            results["wvp"] = {"status": "unconfigured"}; failed = True
        else:
            try:
                client = WvpClient(base, env.get("GB28181_USER", ""), env.get("GB28181_PASS", ""))
                rows = client.devices()
                results["wvp"] = {"status": "ok", "devices": len(rows), "with_geo": sum(r["lat"] is not None for r in rows)}
            except Exception:
                results["wvp"] = {"status": "failed", "hint": "检查WVP版本、服务及凭据"}; failed = True
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(failed or not results)


if __name__ == "__main__":
    raise SystemExit(main())
