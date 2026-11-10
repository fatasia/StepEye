"""V0 续:手套判定迷你评测 —— VLM 输出 vs 人工真值,算一致率与混淆。

用法: python tools/eval_gloves.py <evalset目录>
前置: 目录内有 *.jpg + labels.json;凭据走 .env。
输出: <目录>/eval_result.json + 终端表格。只测违规方向(无 PASS 正样本,见 labels.json 说明)。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from visual.llm_provider import ProviderConfig, VLMProvider  # noqa: E402

PROMPT = (
    "判断画面中的作业人员是否佩戴手套。只输出JSON:"
    '{"verdict":"PASS|NG|UNCERTAIN","confidence":0到1的小数,"reason":"不超过30字"}'
    "画面模糊或看不见手时用 UNCERTAIN。"
)


def main() -> int:
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".v0/evalset")
    labels = json.loads((d / "labels.json").read_text(encoding="utf-8"))["labels"]
    cfg = ProviderConfig.from_env()
    provider = VLMProvider(cfg)
    rows = []
    for name, meta in sorted(labels.items()):
        img = d / name
        b64 = base64.b64encode(img.read_bytes()).decode()
        t0 = time.monotonic()
        out = provider.inspect([b64], PROMPT)
        rows.append({"file": name, "truth": meta["truth"], "note": meta["note"],
                     "pred": out["verdict"], "conf": out["confidence"], "ok": out["ok"],
                     "reason": (out["reason"] or "")[:60], "latency_s": round(time.monotonic() - t0, 1)})
        print(f"  {name}: truth={meta['truth']} pred={out['verdict']} conf={out['confidence']} {rows[-1]['latency_s']}s")
    valid = [r for r in rows if r["ok"]]
    hit = sum(1 for r in valid if r["pred"] == r["truth"])
    # 违规方向漏报:真值 NG 却判 PASS/UNCERTAIN —— EHS 最危险的错误
    missed = [r["file"] for r in valid if r["truth"] == "NG" and r["pred"] != "NG"]
    parse_fail = [r["file"] for r in rows if not r["ok"]]
    lat = sorted(r["latency_s"] for r in rows)
    result = {
        "n": len(rows), "agree": hit, "agree_rate": round(hit / len(valid), 3) if valid else None,
        "missed_violations": missed, "parse_fail": parse_fail,
        "latency_s": {"p50": lat[len(lat) // 2] if lat else None, "max": lat[-1] if lat else None},
        "rows": rows,
    }
    (d / "eval_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
