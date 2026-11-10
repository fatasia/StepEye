"""个体值 I-MR、能力/绩效估计和缺陷 Pareto；不自动签署过程能力。"""
from __future__ import annotations

from collections import Counter
import math
from statistics import mean, stdev

MAX_ROWS = 20000


def number(value, name="value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError(f"{name} 必须是有限数值，绝对值不超过1e100")
    return float(value)


def text(value, name="name") -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 100:
        raise ValueError(f"{name} 必须为1–100字的非空名称")
    return value


def records(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS or any(not isinstance(r, dict) for r in rows):
        raise ValueError("需要1–20000行对象数据")
    return rows


def specifications(lsl=None, usl=None):
    low = number(lsl, "lsl") if lsl is not None else None
    high = number(usl, "usl") if usl is not None else None
    if low is not None and high is not None and low >= high:
        raise ValueError("lsl 必须小于 usl")
    return low, high


def analyze_spc(rows: list[dict], *, lsl=None, usl=None, baseline_count: int | None = None) -> dict:
    """输入顺序即采集顺序，不自动排序/剔除异常；baseline之后固定控制界限。"""
    values = [number(r.get("value")) for r in records(rows)]
    low, high = specifications(lsl, usl)
    n = len(values)
    count = n if baseline_count is None else baseline_count
    if type(count) is not int or not 2 <= count <= n:
        raise ValueError("I-MR基线至少2点，baseline_count不能超过总点数")
    baseline = values[:count]
    center = mean(baseline)
    mr = [abs(b - a) for a, b in zip(values, values[1:])]
    mr_bar = mean(mr[:count-1])
    within_sigma = mr_bar / 1.128  # NIST: d2 for moving range of two successive values.
    overall_mean, overall_sigma = mean(values), stdev(values)
    limits = {"center": center, "lcl": center - 3*within_sigma, "ucl": center + 3*within_sigma}
    mr_limits = {"center": mr_bar, "lcl": 0., "ucl": 3.267*mr_bar}
    defined = within_sigma > 0
    individuals = [{"index": i, "value": v, "phase": "baseline" if i < count else "monitoring",
                    "outside": (v < limits["lcl"] or v > limits["ucl"]) if defined else None}
                   for i, v in enumerate(values)]
    ranges = [{"index": i+1, "value": v, "outside": v > mr_limits["ucl"] if defined else None}
              for i, v in enumerate(mr)]
    signals = [{"chart": "I", "index": p["index"]} for p in individuals if p["outside"]]
    signals += [{"chart": "MR", "index": p["index"]} for p in ranges if p["outside"]]
    def indices(mu, sigma):
        if sigma <= 0:
            return {"spread": None, "centered": None, "lower": None, "upper": None}
        def ratio(a,b):
            result = a/b if b else math.inf
            return result if math.isfinite(result) else None
        lower = ratio(mu-low,3*sigma) if low is not None else None
        upper = ratio(high-mu,3*sigma) if high is not None else None
        available = [v for v in (lower, upper) if v is not None]
        return {"spread": ratio(high-low,6*sigma) if low is not None and high is not None else None,
                "centered": min(available) if available else None, "lower": lower, "upper": upper}
    # Same analysis-period mean; only within vs overall dispersion differs.
    short, overall = indices(overall_mean, within_sigma), indices(overall_mean, overall_sigma)
    warnings = ["采集顺序由调用方提供；独立性、正态性、量测系统及基线稳定性未检验",
                "只检查I图3σ与MR上限，不代表已经通过全部失控规则"]
    if count < 25: warnings.append("基线少于25点，控制界限不稳健")
    if n < 50: warnings.append("能力研究少于50点，只作描述性估计")
    if not defined: warnings.append("基线移动极差为0，不能据此证明过程无变差")
    if signals: warnings.append("发现控制图信号，不能据能力指数判定过程合格")
    below = sum(v < low for v in values) if low is not None else 0
    above = sum(v > high for v in values) if high is not None else 0
    return {"method": "individuals_moving_range", "status": "review_required", "n": n,
            "baseline_count": count, "mean": overall_mean, "baseline_mean": center,
            "within_sigma": within_sigma, "overall_sigma": overall_sigma,
            "assumptions": "unverified", "production_approved": False,
            "control_status": "undefined_zero_variation" if not defined else "signals_detected" if signals else "no_rule1_signals",
            "individuals": {"limits": limits if defined else None, "points": individuals},
            "moving_range": {"limits": mr_limits if defined else None, "points": ranges}, "signals": signals,
            "capability": {"interpretation": "descriptive_only", "lsl": low, "usl": high,
                           "cp": short["spread"], "cpk": short["centered"], "cpl": short["lower"], "cpu": short["upper"],
                           "pp": overall["spread"], "ppk": overall["centered"], "ppl": overall["lower"], "ppu": overall["upper"],
                           "cp_scope": "all-value mean; within sigma=baseline MRbar/1.128", "pp_scope": "all values; sample SD ddof=1"},
            "observed_spec": {"below": below, "above": above, "outside": below+above,
                              "fraction": (below+above)/n if low is not None or high is not None else None},
            "warnings": warnings}


def defect_pareto(rows: list[dict]) -> dict:
    """按缺陷次数排名；次数不等于不良件数，禁止用它推断良率。"""
    counts = Counter()
    for row in records(rows):
        name, count = text(row.get("defect"), "defect"), row.get("count", 1)
        if type(count) is not int or not 0 <= count <= 10**9:
            raise ValueError("count 必须为0–1e9的整数")
        counts[name] += count
    total, cumulative, items = sum(counts.values()), 0, []
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        cumulative += count
        items.append({"defect": name, "count": count, "fraction": count/total if total else None,
                      "cumulative_fraction": cumulative/total if total else None})
    return {"method": "defect_pareto", "status": "descriptive" if total else "no_defects_counted",
            "total_defects": total, "items": items, "yield": None,
            "warnings": ["缺陷可在同一件产品重复计数；未提供检验件数，不能计算良率或DPMO"]}
