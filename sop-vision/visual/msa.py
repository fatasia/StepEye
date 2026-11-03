"""平衡交叉 GR&R（保留交互）及重复分类一致性；无假设检验即不作量具放行。"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
from statistics import mean

from .spc import number, records, specifications, text


def _crossed(rows, field):
    cells, parts, operators, trials = {}, set(), set(), set()
    for row in records(rows):
        part, operator = text(row.get("part"), "part"), text(row.get("operator"), "operator")
        trial = row.get("trial")
        if isinstance(trial, int) and not isinstance(trial, bool): trial = str(trial)
        trial = text(trial, "trial")
        key = (part, operator, trial)
        if key in cells: raise ValueError("同一part/operator/trial有重复记录")
        cells[key] = number(row.get(field)) if field == "value" else text(row.get(field), field)
        parts.add(part); operators.add(operator); trials.add(trial)
    p, o, r = len(parts), len(operators), len(trials)
    if o > 50 or p > 1000 or r > 1000:
        raise ValueError("最多50测量者、1000零件、1000次重复")
    if min(p, o, r) < 2:
        raise ValueError("需要至少2个零件、2个测量者/系统、2次重复的完整交叉设计")
    if len(cells) != p*o*r:
        raise ValueError("只支持平衡完整交叉数据；存在缺测、不同重复次数或嵌套设计")
    return cells, sorted(parts), sorted(operators), sorted(trials)


def analyze_grr(rows: list[dict], *, lsl=None, usl=None) -> dict:
    cells, parts, operators, trials = _crossed(rows, "value")
    low, high = specifications(lsl, usl)
    if (low is None) != (high is None):
        raise ValueError("GRR %Tolerance需要同时给出lsl与usl，或都不提供")
    p, o, r = len(parts), len(operators), len(trials)
    grand = mean(cells.values())
    cm = {(a, b): mean(cells[a, b, t] for t in trials) for a in parts for b in operators}
    pm = {a: mean(cm[a, b] for b in operators) for a in parts}
    om = {b: mean(cm[a, b] for a in parts) for b in operators}
    ss = {"part": o*r*sum((v-grand)**2 for v in pm.values()),
          "operator": p*r*sum((v-grand)**2 for v in om.values()),
          "interaction": r*sum((cm[a,b]-pm[a]-om[b]+grand)**2 for a in parts for b in operators),
          "repeatability": sum((value-cm[a,b])**2 for (a,b,t), value in cells.items())}
    df = {"part": p-1, "operator": o-1, "interaction": (p-1)*(o-1), "repeatability": p*o*(r-1)}
    ms = {k: ss[k]/df[k] for k in ss}
    raw = {"repeatability": ms["repeatability"], "interaction": (ms["interaction"]-ms["repeatability"])/r,
           "operator": (ms["operator"]-ms["interaction"])/(p*r),
           "part_to_part": (ms["part"]-ms["interaction"])/(o*r)}
    variances = {k: max(0., v) for k, v in raw.items()}
    variances["reproducibility"] = variances["operator"] + variances["interaction"]
    variances["grr"] = variances["repeatability"] + variances["reproducibility"]
    total = variances["grr"] + variances["part_to_part"]
    variances["total"] = total
    def tolerance_percent(var):
        if low is None: return None
        value = 600*math.sqrt(var)/(high-low)
        return value if math.isfinite(value) else None
    components = {name: {"variance": var, "sd": math.sqrt(var), "study_variation_6sigma": 6*math.sqrt(var),
                         "percent_contribution": 100*var/total if total else None,
                         "percent_study_variation": 100*math.sqrt(var/total) if total else None,
                         "percent_tolerance": tolerance_percent(var)}
                  for name, var in variances.items()}
    grr, part = variances["grr"], variances["part_to_part"]
    discrimination = 1.41*(math.sqrt(part)/math.sqrt(grr)) if grr > 0 else None
    ndc = max(1, math.floor(discrimination)) if discrimination is not None and math.isfinite(discrimination) else None
    warnings = ["交互项始终保留，未做p值检验或自动合并；负方差分量按0截断并保留原估计",
                "独立性、正态残差、同方差、测量随机化及零件代表性未检验；本报告不自动判定量具合格"]
    if p < 10 or o < 3: warnings.append("少于10零件或3测量者；用于软件分析，不是充分的量测系统验收设计")
    if total == 0: warnings.append("全部变差为0，无法估计占比或有效分辨类别数")
    if grr == 0: warnings.append("估计GRR为0，不能由有限重复数据证明测量无误差")
    return {"method": "balanced_crossed_anova_with_interaction", "status": "review_required",
            "design": {"parts": p, "operators": o, "trials": r, "observations": len(cells)},
            "mean": grand, "anova": [{"source": k, "ss": ss[k], "df": df[k], "ms": ms[k], "p_value": None} for k in ss],
            "raw_variance_components": raw, "clamped_components": [k for k,v in raw.items() if v < 0],
            "components": components, "ndc": ndc,
            "ndc_status": "estimated" if ndc is not None else "undefined_zero_variation_or_numeric_range",
            "assumptions": "unverified", "production_approved": False, "warnings": warnings}


def _kappa(left, right):
    n = len(left)
    agree = sum(a == b for a,b in zip(left, right))/n
    a, b = Counter(left), Counter(right)
    expected = sum(a[k]*b[k] for k in set(a)|set(b))/(n*n)
    return {"observations": n, "agreement": agree, "expected_agreement": expected,
            "kappa": (agree-expected)/(1-expected) if expected < 1 else None,
            "kappa_status": "estimated" if expected < 1 else "undefined_single_category"}


def analyze_agreement(rows: list[dict]) -> dict:
    cells, parts, operators, trials = _crossed(rows, "label")
    references = {}
    for row in rows:
        if row.get("reference") is not None and row.get("reference") != "":
            reference = text(row["reference"], "reference")
            if row["part"] in references and references[row["part"]] != reference:
                raise ValueError("同一零件的reference必须一致")
            references[row["part"]] = reference
    within = []
    for op in operators:
        stable = sum(len({cells[a,op,t] for t in trials}) == 1 for a in parts)
        tested = [cells[a,op,t] == references[a] for a in parts if a in references for t in trials]
        within.append({"operator": op, "repeatable_parts": stable, "parts": len(parts), "repeatability": stable/len(parts),
                       "reference_observations": len(tested), "reference_accuracy": sum(tested)/len(tested) if tested else None})
    pairs = []
    for a,b in combinations(operators, 2):
        pairs.append({"operators": [a,b], **_kappa([cells[p,a,t] for p in parts for t in trials],
                                                   [cells[p,b,t] for p in parts for t in trials])})
    matched = sum(len({cells[a,op,t] for op in operators}) == 1 for a in parts for t in trials)
    strict = sum(len({cells[a,op,t] for op in operators for t in trials}) == 1 for a in parts)
    return {"method": "categorical_repeated_crossed_agreement", "status": "review_required",
            "design": {"parts": len(parts), "operators": len(operators), "trials": len(trials)},
            "within_operator": within, "between_operator_per_trial": matched/(len(parts)*len(trials)),
            "all_operators_all_trials_part_agreement": strict/len(parts), "pairwise_cohen_kappa": pairs,
            "reference_parts": len(references), "reference_coverage": len(references)/len(parts),
            "accuracy_status": "not_measured_without_reference" if not references else "partial_reference" if len(references)<len(parts) else "reference_provided",
            "production_approved": False,
            "warnings": ["一致性不是准确率；reference仅为导入的参考标签，未验证其真值质量",
                         "Kappa按part/trial配对，未给置信区间；重复观察不独立，不能据点估计宣称显著",
                         "未检验随机化、盲测、记忆效应和零件代表性"]}
