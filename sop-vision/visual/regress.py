"""评测回归:固定样本集一键跑分,提示词/模型改动后自动对比,防判定质量退化。

用法:
  python -m visual regress <manifest.yaml> --data <样本目录> [--out result.json] [--against prev.json]
manifest 格式:
  name: glove-mini
  prompt: |
    判断...
  cases:
    - {image: eval_00.jpg, expect: NG}
    - {image: eval_01.jpg, expect: NG}
判定输出与 expect 按大小写不敏感比对;与上一份 result 对比输出回归/改进清单。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import yaml

from .llm_provider import ProviderConfig, VLMProvider


def load_manifest(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    name = str(raw.get("name") or path.stem)
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"{path}: 缺少全局 prompt")
    cases = raw.get("cases") or []
    if not cases:
        raise ValueError(f"{path}: cases 为空")
    for i, c in enumerate(cases):
        if not c.get("image") or not c.get("expect"):
            raise ValueError(f"{path}: 第 {i + 1} 个 case 缺 image 或 expect")
    return {"name": name, "prompt": prompt, "cases": cases}


def run_manifest(manifest: dict, provider: VLMProvider, data_dir: Path) -> list[dict]:
    rows = []
    for i, case in enumerate(manifest["cases"]):
        img = data_dir / case["image"]
        if not img.is_file():
            raise FileNotFoundError(f"样本不存在: {img}(相对 --data 目录)")
        b64 = base64.b64encode(img.read_bytes()).decode()
        t0 = time.monotonic()
        out = provider.inspect([b64], manifest["prompt"])
        rows.append({
            "image": case["image"], "expect": str(case["expect"]).upper(),
            "pred": str(out["verdict"]).upper() if out["ok"] else "ERROR",
            "conf": out["confidence"], "ok": out["ok"], "reason": (out["reason"] or "")[:80],
            "latency_s": round(time.monotonic() - t0, 1),
        })
        print(f"  [{i + 1}/{len(manifest['cases'])}] {case['image']}: expect={rows[-1]['expect']} pred={rows[-1]['pred']} {rows[-1]['latency_s']}s")
    return rows


def summarize(manifest_name: str, cfg: ProviderConfig, rows: list[dict]) -> dict:
    valid = [r for r in rows if r["pred"] != "ERROR"]
    agree = sum(1 for r in valid if r["pred"] == r["expect"])
    return {
        "name": manifest_name, "model": cfg.model, "base_url": cfg.base_url,
        "n": len(rows), "agree": agree, "agree_rate": round(agree / len(valid), 3) if valid else None,
        "error_n": len(rows) - len(valid),
        "rows": rows,
    }


def diff_runs(before: dict, after: dict) -> dict:
    """两份 result 对比:回归=原来对现在错;改进=原来错现在对。"""
    def by_img(res: dict) -> dict:
        return {r["image"]: r for r in res.get("rows", [])}

    b, a = by_img(before), by_img(after)
    regressions, improvements, changed = [], [], []
    for img in sorted(set(b) & set(a)):
        rb, ra = b[img], a[img]
        ok_b, ok_a = rb["pred"] == rb["expect"], ra["pred"] == ra["expect"]
        if ok_b and not ok_a:
            regressions.append({"image": img, "expect": rb["expect"], "before": rb["pred"], "after": ra["pred"]})
        elif not ok_b and ok_a:
            improvements.append({"image": img, "expect": rb["expect"], "before": rb["pred"], "after": ra["pred"]})
        elif rb["pred"] != ra["pred"]:
            changed.append({"image": img, "expect": rb["expect"], "before": rb["pred"], "after": ra["pred"]})
    delta = None
    if before.get("agree_rate") is not None and after.get("agree_rate") is not None:
        delta = round(after["agree_rate"] - before["agree_rate"], 3)
    return {"regressions": regressions, "improvements": improvements, "changed": changed,
            "agree_rate": {"before": before.get("agree_rate"), "after": after.get("agree_rate"), "delta": delta}}


def _regress(args) -> int:
    manifest = load_manifest(Path(args.manifest))
    data_dir = Path(args.data or Path(args.manifest).parent)
    cfg = ProviderConfig.from_env()
    if not cfg.api_key:
        print("缺少 ZHIPU_API_KEY(.env)", file=sys.stderr)
        return 2
    provider = VLMProvider(cfg)
    print(f"回归集 {manifest['name']}  {len(manifest['cases'])} 例  模型 {cfg.model}")
    rows = run_manifest(manifest, provider, data_dir)
    result = summarize(manifest["name"], cfg, rows)
    if args.against:
        prev = json.loads(Path(args.against).read_text(encoding="utf-8"))
        result["diff"] = diff_runs(prev, result)
        d = result["diff"]
        print(f"对比 {Path(args.against).name}: 一致率 {d['agree_rate']['before']} -> {d['agree_rate']['after']}(Δ{d['agree_rate']['delta']})")
        for tag, key in (("回归(原来对现在错)", "regressions"), ("改进", "improvements"), ("结论变化", "changed")):
            for r in d[key]:
                print(f"  {tag}: {r['image']} expect={r['expect']} {r['before']} -> {r['after']}")
    out = Path(args.out) if args.out else data_dir / "regress_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"一致率 {result['agree_rate']}({result['agree']}/{result['n']},错误 {result['error_n']})  -> {out}")
    return 0 if result["agree_rate"] == 1.0 else 1
