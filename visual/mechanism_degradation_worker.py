"""FEMTO 相似性 RUL worker：读取固定 SHA 参考序列，纯 numpy 计算趋势级 RUL。

方法与 docs/DEGRADATION-RUL-2026-11-05.md 基线一致：观测段 HI（对数 RMS 比）
min-max 归一化后与训练试验全程 HI 等长片段匹配（步长 5 分钟），最小欧氏距离
片段的剩余时长即相似性 RUL；另输出线性外推对照（已知对突发失效原理性失效）。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

SIM_STRIDE_MIN = 5.0
MIN_POINTS = 5


def monotonic_front(hi: np.ndarray) -> np.ndarray:
    return np.maximum.accumulate(hi)


def normalize(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    return (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)


def similarity_rul(obs_hi: np.ndarray, reference: dict) -> dict:
    obs = normalize(monotonic_front(obs_hi))
    best = None
    for bearing, pts in reference["train_hi"].items():
        series = monotonic_front(np.asarray([p["hi"] for p in pts], dtype=float))
        times = np.asarray([p["t_min"] for p in pts], dtype=float)
        if len(series) < len(obs):
            continue
        for start in range(0, len(series) - len(obs) + 1):
            window = series[start:start + len(obs)]
            span = times[start + len(obs) - 1] - times[start]
            if span < (len(obs) - 1) * 0.5:  # 相邻点约 1 分钟；剔除稀疏异常片段
                continue
            if times[start + len(obs) - 1] < times[start] + (len(obs) - 1):
                continue
            distance = float(np.linalg.norm(normalize(window) - obs))
            if best is None or distance < best["distance"]:
                best = {"bearing": bearing, "distance": round(distance, 6),
                        "rul_min": float(times[-1] - times[start + len(obs) - 1])}
    return best


def extrapolation_rul(obs_hi: np.ndarray, reference: dict) -> dict:
    """线性外推到失效阈值；斜率非正或发散时如实返回 None 并给原因。"""
    n = len(obs_hi)
    x = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(x, obs_hi, 1)
    if slope <= 1e-9:
        return None
    target = reference["threshold_hi"]
    if obs_hi[-1] >= target:
        return {"rul_min": 0.0, "capped": False, "reason": "threshold_reached"}
    steps = (target - intercept - slope * (n - 1)) / slope
    rul = steps  # 每步约 1 分钟
    capped = False
    cap = reference["rul_cap_min"]
    if rul > cap:
        rul, capped = cap, True
    return {"rul_min": float(max(rul, 0.0)), "capped": capped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    reference = json.loads((Path(request["root"]) / "models/mechanism/femto-degradation/reference.json").read_text(encoding="utf-8"))
    results = []
    for window in request["body"]["windows"]:
        rms = np.asarray(window["rms_values"], dtype=float)
        if not np.all(np.isfinite(rms)) or np.any(rms <= 0):
            raise SystemExit("rms_values must be positive finite")
        hi = np.log(rms / window["baseline_rms"])
        match = similarity_rul(hi, reference)
        extrapolation = extrapolation_rul(hi, reference)
        if match is None:
            raise SystemExit("observation longer than every training trial")
        uncertainty = reference["threshold_uncertainty"]
        results.append({
            "unit_id": window["unit_id"],
            "rul_min": match["rul_min"],
            "method": "similarity",
            "matched_bearing": match["bearing"],
            "match_distance": match["distance"],
            "threshold_hi": reference["threshold_hi"],
            "threshold_uncertainty_min": min(uncertainty),
            "threshold_uncertainty_max": max(uncertainty),
            "extrapolation": extrapolation,
            "capped": bool(extrapolation and extrapolation.get("capped")),
            "points": int(len(rms)),
        })
    print(json.dumps({
        "status": "evidence_only",
        "dataset": reference["dataset"],
        "license": reference["license"],
        "unit": "minute",
        "scope": "trend-level similarity RUL on bearing vibration RMS; not a precision RUL model",
        "windows": results,
    }, allow_nan=False))


if __name__ == "__main__":
    main()
