"""V5 冒烟:首件 OK/NG 两步判定 —— 与 OK 参考共有=正常,OK 中不存在的缺陷才判 NG。

用法: python tools/probe_first_article.py <参考目录> <待检目录>
参考目录含 ok/*.jpg 与 ng/*.jpg;待检目录含 probe_*.jpg + truth.json。
两步:①列差异 ②按规则判定。输出准确率与延迟,回填 docs/V0-SMOKE.md。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from visual.llm_provider import ProviderConfig, VLMProvider  # noqa: E402

DIFF_PROMPT = (
    "前 3 张是首件 OK 参考图,最后 1 张是待检图。列出待检图与 OK 参考图的所有可见差异"
    "(按位置描述,例如『右上角少了一个圆孔』)。只输出JSON:{\"differences\":[\"...\"]}"
)
VERDICT_PROMPT = (
    "前 3 张是首件 OK 参考图,中间 3 张是典型 NG 参考图(缺件类缺陷),最后 1 张是待检图。"
    "判定规则:与 OK 参考共有的特征属于正常工艺特征,不算缺陷;只有 OK 基准中不存在、"
    "且与 NG 参考同类的缺失/异常才判 NG。待检差异:{differences}\n"
    '只输出JSON:{"verdict":"PASS|NG|UNCERTAIN","confidence":0到1,"reason":"不超过30字"}'
)


def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def main() -> int:
    refs, probes = Path(sys.argv[1]), Path(sys.argv[2])
    ok_imgs = sorted((refs / "ok").glob("*.jpg"))
    ng_imgs = sorted((refs / "ng").glob("*.jpg"))
    truth = json.loads((probes / "truth.json").read_text(encoding="utf-8"))
    provider = VLMProvider(ProviderConfig.from_env())
    ok_b64 = [b64(p) for p in ok_imgs]
    ng_b64 = [b64(p) for p in ng_imgs]
    rows, t_all = [], time.monotonic()
    for probe, gt in sorted(truth.items()):
        pb = b64(probes / probe)
        t0 = time.monotonic()
        step1 = provider.inspect(ok_b64 + [pb], DIFF_PROMPT)
        diffs = "无明显差异" if not step1["ok"] else json.dumps(step1.get("raw"), ensure_ascii=False)[:400]
        # 差异列表原文喂给第二步(从 raw 提炼);解析失败时降级为无差异提示,第二步仍可独立判断
        try:
            diffs = json.dumps(json.loads(step1["raw"])["differences"], ensure_ascii=False) if step1["ok"] else "无明显差异"
        except Exception:  # noqa: BLE001 —— 差异解析失败不阻塞判定
            diffs = "无明显差异"
        step2 = provider.inspect(ok_b64 + ng_b64 + [pb], VERDICT_PROMPT.replace("{differences}", diffs))
        rows.append({"probe": probe, "truth": gt, "pred": step2["verdict"], "conf": step2["confidence"],
                     "ok": step2["ok"], "reason": (step2["reason"] or "")[:60],
                     "latency_s": round(time.monotonic() - t0, 1)})
        print(f"  {probe}: truth={gt} pred={step2['verdict']} conf={step2['confidence']} {rows[-1]['latency_s']}s | {rows[-1]['reason']}")
    valid = [r for r in rows if r["ok"]]
    correct = sum(1 for r in valid if r["pred"] == r["truth"])
    lat = sorted(r["latency_s"] for r in rows)
    out = {"n": len(rows), "correct": correct, "accuracy": round(correct / len(valid), 3) if valid else None,
           "wrong": [r for r in valid if r["pred"] != r["truth"]],
           "latency_s": {"p50": lat[len(lat) // 2] if lat else None, "max": lat[-1] if lat else None,
                         "total": round(time.monotonic() - t_all, 1)}, "rows": rows}
    (probes / "probe_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
