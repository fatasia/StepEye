"""Inspection decision aggregation: an accepted image cannot hide a rejected one."""
from __future__ import annotations

import math


def aggregate_judgements(results: list[dict]) -> tuple[str, float, str]:
    """NG takes precedence, then UNCERTAIN; PASS requires every image to pass.

    Confidence is the least confident result within the selected verdict, not a
    fabricated probability for the complete inspection. Malformed results fail.
    """
    if not results:
        raise ValueError("没有可汇总的图片判定")
    priority = {"NG": 0, "UNCERTAIN": 1, "PASS": 2}
    checked = []
    for result in results:
        verdict, confidence = result.get("verdict"), result.get("confidence")
        if verdict not in priority or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("图片判定或置信度无效")
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("图片置信度须为0到1的有限数值")
        reason = result.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError("图片判定原因格式无效")
        checked.append((verdict, float(confidence), reason))
    return min(checked, key=lambda item: (priority[item[0]], item[1]))
