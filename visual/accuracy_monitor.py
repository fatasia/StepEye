"""Compare reviewed holdout reports; observational quality change, never rollback."""
from __future__ import annotations

import math


def _metrics(report):
    if report.get("kind") != "detection_validation":
        raise ValueError("仅支持检测验证报告")
    evaluation = report.get("evaluation") or {}
    if evaluation.get("metric") != "single_operating_point":
        raise ValueError("报告缺少单工作点评测")
    frames = report.get("frames") or []
    if (not frames or evaluation.get("frames") != len(frames)
            or any(frame.get("reviewed") is not True for frame in frames)):
        raise ValueError("报告缺少已复核逐帧真值")
    classes = evaluation.get("classes") or {}
    if not classes:
        raise ValueError("报告缺少逐类计数")
    result = {}
    for name, counts in classes.items():
        if any(type(counts.get(key)) is not int or counts[key] < 0 for key in ("tp", "fp", "fn")):
            raise ValueError("报告 TP/FP/FN 计数无效")
        tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
        result[name] = {"tp": tp, "fp": fp, "fn": fn,
                        "predictions": tp + fp, "truth": tp + fn,
                        "precision": tp / (tp + fp) if tp + fp else None,
                        "recall": tp / (tp + fn) if tp + fn else None}
    return evaluation, result


def compare_reports(baseline, current, *, min_samples=20, min_drop=.1):
    """Drops are percentage-point changes, not inferred causality/significance."""
    if type(min_samples) is not int or not 1 <= min_samples <= 10000:
        raise ValueError("最小样本量须为1至10000")
    if type(min_drop) not in (int, float) or not math.isfinite(min_drop) or not 0 < min_drop <= 1:
        raise ValueError("下降阈值须大于0且不超过1")
    if baseline.get("id") == current.get("id"):
        raise ValueError("基准与当前不能是同一报告")
    if (not baseline.get("model_sha256") or baseline["model_sha256"] != current.get("model_sha256")
            or not baseline.get("adapter") or baseline["adapter"] != current.get("adapter")):
        raise ValueError("两个报告须使用相同模型 SHA 和适配器")
    before, old = _metrics(baseline)
    after, new = _metrics(current)
    if list(old) != list(new):
        raise ValueError("两个报告须使用相同类别及顺序")
    for key in ("confidence_threshold", "iou_threshold"):
        value = before.get(key)
        if (type(value) not in (int, float) or not 0 < value <= 1 or value != after.get(key)):
            raise ValueError("两个报告须使用相同置信度和 IoU 工作点")
    frame_support = min(before["frames"], after["frames"]) >= min_samples
    classes = {}
    for name in old:
        changes = {}
        for metric, denominator in (("precision", "predictions"), ("recall", "truth")):
            sufficient = frame_support and min(old[name][denominator], new[name][denominator]) >= min_samples
            delta = (new[name][metric] - old[name][metric]
                     if old[name][metric] is not None and new[name][metric] is not None else None)
            changes[metric] = {"delta": delta, "sufficient": sufficient,
                               "observed_drop": sufficient and delta is not None and -delta >= min_drop - 1e-12}
        status = ("observed_drop" if any(m["observed_drop"] for m in changes.values()) else
                  "insufficient_data" if not all(m["sufficient"] for m in changes.values()) else "no_observed_drop")
        classes[name] = {"baseline": old[name], "current": new[name], "metrics": changes, "status": status}
    status = ("observed_drop" if any(c["status"] == "observed_drop" for c in classes.values()) else
              "insufficient_data" if any(c["status"] == "insufficient_data" for c in classes.values()) else "no_observed_drop")
    return {"schema_version": 1, "kind": "reviewed_accuracy_comparison", "status": status,
            "baseline_id": baseline["id"], "current_id": current["id"],
            "model_sha256": current["model_sha256"], "adapter": current["adapter"],
            "operating_point": {k: after[k] for k in ("confidence_threshold", "iou_threshold")},
            "min_samples": min_samples, "min_drop": min_drop,
            "baseline_frames": before["frames"], "current_frames": after["frames"],
            "baseline_dataset_sha256": baseline.get("dataset_sha256"),
            "current_dataset_sha256": current.get("dataset_sha256"),
            "dataset_relation": "same_dataset" if baseline.get("dataset_sha256") == current.get("dataset_sha256") else "different_datasets",
            "prediction_protocol_changed": baseline.get("prediction_protocol") != current.get("prediction_protocol"),
            "classes": classes, "automatic_rollback": False,
            "scope": "Observed change on reviewed samples; dataset difficulty, annotation and capture changes are not separated from model degradation. No significance or production acceptance claim."}
