"""评测回归:固定样本集一键跑分,提示词/模型改动后自动对比,防判定质量退化。

用法:
  python -m visual regress <manifest.yaml> --data <样本目录> [--out result.json] [--against prev.json]
                           [--store] [--card card.md]
manifest 格式:
  name: glove-mini
  version: v3            # 可选;回归集版本号,入库用
  prompt: |
    判断...
  cases:
    - {image: eval_00.jpg, expect: NG}
    - {image: eval_01.jpg, expect: NG}
判定输出与 expect 按大小写不敏感比对;与上一份 result 对比输出回归/改进清单。
--store 入库(SQLite,版本化);--card 产出「能力边界卡」——给客户的期望管理文档。
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
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


def manifest_hash(manifest_path: Path, data_dir: Path) -> str:
    """回归集指纹:清单文本 + 样本内容哈希——样本变一个字节,指纹就变。"""
    h = hashlib.sha256()
    h.update(manifest_path.read_bytes())
    manifest = load_manifest(manifest_path)
    for c in manifest["cases"]:
        img = data_dir / c["image"]
        if img.is_file():
            h.update(img.read_bytes())
    return h.hexdigest()[:16]


class RegressStore:
    """回归结果入库(版本化);接口对齐 CycleStore 模式。"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS regress_runs (id TEXT PRIMARY KEY, name TEXT, version TEXT,"
            " agree_rate REAL, created TEXT, body TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def save(self, rec: dict) -> str:
        rid = f"{rec['name']}-{rec.get('version') or 'base'}-{rec['created']}"
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO regress_runs VALUES (?,?,?,?,?,?)",
                             (rid, rec["name"], rec.get("version"), rec.get("agree_rate"),
                              rec["created"], json.dumps(rec, ensure_ascii=False)))
            self._db.commit()
        return rid

    def list(self, name: str | None = None, limit: int = 50) -> list[dict]:
        q, args = "SELECT body FROM regress_runs", []
        if name:
            q += " WHERE name=?"
            args.append(name)
        q += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, rid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM regress_runs WHERE id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None


def boundary_card(result: dict, rerun_cmd: str, fp: str) -> str:
    """能力边界卡:本场景在固定回归集上的实测表现 + 复跑命令。承诺的数字=卡上的数字。"""
    rows = result.get("rows", [])
    img_name = lambda r: r.get("image") or r.get("file") or "?"  # noqa: E731 —— 回归行/样本行两种形态
    missed = [img_name(r) for r in rows if r["expect"] == "NG" and r["pred"] not in ("NG", "ERROR")]
    false_alarms = [img_name(r) for r in rows if r["expect"] == "PASS" and r["pred"] not in ("PASS", "ERROR")]
    lat = sorted(r["latency_s"] for r in rows if r.get("latency_s") is not None)
    lines = [
        f"# 能力边界卡 · {result['name']}",
        "",
        f"- 回归集版本:{result.get('version') or 'base'} · 样本指纹:{fp}",
        f"- 模型/端点:{result['model']} @ {result['base_url']}",
        f"- 生成时间:{result['created']}",
        f"- 样本数:{result['n']} · 一致率:**{result['agree_rate']}**({result['agree']}/{result['n']},解析错误 {result['error_n']})",
        f"- 违规漏报(NG 判成非 NG):**{len(missed)}** {missed[:5] if missed else ''}".rstrip(),
        f"- 误报(PASS 判成非 PASS):**{len(false_alarms)}** {false_alarms[:5] if false_alarms else ''}".rstrip(),
        f"- 延迟:P50 {lat[len(lat) // 2] if lat else '—'}s / max {lat[-1] if lat else '—'}s",
        "",
        "## 复跑命令(任何第三方可复算)",
        "",
        "```",
        rerun_cmd,
        "```",
        "",
        "> 本卡验证的是**判定一致性**,不是准确率:样本无独立人工真值复核时不可外推到现场准确率。",
        "> 正式验收指标须按影子模式运行 ≥2 周后另行统计。",
    ]
    return "\n".join(lines)


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
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    data_dir = Path(args.data or manifest_path.parent)
    cfg = ProviderConfig.from_env()
    if not cfg.api_key:
        print("缺少 ZHIPU_API_KEY(.env)", file=sys.stderr)
        return 2
    provider = VLMProvider(cfg)
    print(f"回归集 {manifest['name']}  {len(manifest['cases'])} 例  模型 {cfg.model}")
    rows = run_manifest(manifest, provider, data_dir)
    result = summarize(manifest["name"], cfg, rows)
    result["version"] = manifest.get("version")
    result["created"] = datetime.now().isoformat(timespec="seconds")
    fp = manifest_hash(manifest_path, data_dir)
    result["manifest_hash"] = fp
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
    if args.store:
        store = RegressStore(data_dir / "regress.db")
        rid = store.save(result)
        store.close()
        print(f"已入库: {rid}")
    if args.card:
        rerun = f"python -m visual regress {manifest_path.name} --data {data_dir}"
        Path(args.card).write_text(boundary_card(result, rerun, fp), encoding="utf-8")
        print(f"能力边界卡 -> {args.card}")
    print(f"一致率 {result['agree_rate']}({result['agree']}/{result['n']},错误 {result['error_n']})  -> {out}")
    return 0 if result["agree_rate"] == 1.0 else 1
