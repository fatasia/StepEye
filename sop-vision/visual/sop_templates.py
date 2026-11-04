"""行业 SOP 工步模板：导入用静态骨架，字段与 dsl.Step 严格对齐。

模板提供标准步骤骨架与建议时长，不是合规判定标准；区域占位符统一为
「待映射:*」前缀，接入真实工位前必须用 map_zones 替换为工位实际区域，
否则 StationConfig 的区域引用校验会拒绝。时长/顺序需按产线实际调整。
"""
from __future__ import annotations

from .dsl import Step

PLACEHOLDER_PREFIX = "待映射:"

# evidence 通道约定：handling=手在作业区停留、material=物件状态、visual=VLM 判定、
# signal=设备信号。占位区域刻意带前缀，防止被当成真实区域直接落配置。
_T = [
    # ---- 基础工步（跨行业复用） ----
    {"id": "load", "name": "上料", "channel": "material", "state": "在位",
     "zone": "上料区", "timeout_s": 120, "action_hint": "取件放置"},
    {"id": "process", "name": "作业", "channel": "handling", "zone": "作业区",
     "min_ms": 500, "timeout_s": 300, "action_hint": "装配作业"},
    {"id": "visual-check", "name": "目视检查", "channel": "visual",
     "prompt": "画面中的工件外观无明显缺陷", "timeout_s": 60, "action_hint": "检查确认"},
    {"id": "label", "name": "贴标", "channel": "material", "state": "标签在位",
     "zone": "贴标位", "timeout_s": 60, "action_hint": "贴附标签"},
    {"id": "unload", "name": "下料", "channel": "material", "state": "成品在位",
     "zone": "下料区", "timeout_s": 120, "action_hint": "取件放入"},
    {"id": "pack", "name": "包装", "channel": "material", "state": "包装完成",
     "zone": "包装位", "timeout_s": 180, "action_hint": "装箱封口"},
    {"id": "equipment-check", "name": "设备点检", "channel": "signal",
     "signal": "equipment_ok", "value": True, "timeout_s": 60, "action_hint": "设备状态确认"},
    {"id": "fasten", "name": "锁附", "channel": "handling", "zone": "锁附位",
     "min_ms": 800, "timeout_s": 120, "action_hint": "拧紧锁附"},
    {"id": "weld", "name": "焊接", "channel": "handling", "zone": "焊接位",
     "min_ms": 1000, "timeout_s": 180, "action_hint": "焊接作业"},
]

# 行业组合：steps 引用 _T 的 id，可带 after 覆盖（None=紧跟上一步，[]=并行起步）。
TEMPLATES: list[dict] = [
    {"id": "3c-assembly", "industry": "3C", "name": "3C 装配线",
     "description": "上料 → 锁附 → 目视检查 → 下料",
     "steps": ["load", "fasten", "visual-check", "unload"]},
    {"id": "3c-pack", "industry": "3C", "name": "3C 包装工位",
     "description": "贴标 → 包装", "steps": ["label", "pack"]},
    {"id": "battery-module", "industry": "锂电", "name": "电池模组工段",
     "description": "上料 → 焊接 → 目视检查 → 下料",
     "steps": ["load", "weld", "visual-check", "unload"]},
    {"id": "battery-pack", "industry": "锂电", "name": "电池包线尾",
     "description": "设备点检 → 包装", "steps": ["equipment-check", "pack"]},
    {"id": "auto-ecu", "industry": "汽车电子", "name": "汽车电子控制器",
     "description": "上料 → 锁附 → 贴标 → 下料",
     "steps": ["load", "fasten", "label", "unload"]},
    {"id": "auto-incoming", "industry": "汽车电子", "name": "汽车电子来料点检",
     "description": "设备点检 → 目视检查", "steps": ["equipment-check", "visual-check"]},
    {"id": "wh-inbound", "industry": "仓储物流", "name": "仓储收货上架",
     "description": "上料 → 目视检查", "steps": ["load", "visual-check"]},
    {"id": "wh-picking", "industry": "仓储物流", "name": "仓储拣选复核",
     "description": "目视检查 → 包装", "steps": ["visual-check", "pack"]},
]


def _skeleton(entry: dict) -> dict:
    channel = entry["channel"]
    if channel == "material":
        evidence = {"all": [{"obj_state": {"zone": PLACEHOLDER_PREFIX + entry["zone"],
                                           "state": entry["state"]}}]}
    elif channel == "handling":
        evidence = {"all": [{"hand_dwell": {"zone": PLACEHOLDER_PREFIX + entry["zone"],
                                            "min_ms": entry["min_ms"]}}]}
    elif channel == "visual":
        evidence = {"all": [{"vlm_check": {"prompt": entry["prompt"]}}]}
    else:
        evidence = {"all": [{"signal": {"name": entry["signal"], "value": entry["value"]}}]}
    step = {"id": entry["id"], "name": entry["name"], "evidence": evidence,
            "timeout_s": entry["timeout_s"]}
    # 工步骨架默认串行；首步显式无前驱，避免依赖调用方配置顺序。
    return step


def list_templates(industry: str = "") -> list[dict]:
    if industry and industry not in {t["industry"] for t in TEMPLATES}:
        raise ValueError(f"未知行业：{industry}；可选：{sorted({t['industry'] for t in TEMPLATES})}")
    return [t for t in TEMPLATES if not industry or t["industry"] == industry]


def template_steps(template_id: str, zone_map: dict[str, str] | None = None) -> list[dict]:
    """导出 dsl.Step 兼容的步骤字典列表（按模板顺序串行）。

    zone_map 把占位区域名（不含前缀）映射到工位实际区域；未映射的占位符保留
    「待映射:」前缀原样输出，方便调用方发现遗漏而不是落到错误区域。
    """
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if template is None:
        raise ValueError("模板不存在")
    zone_map = zone_map or {}
    unknown = sorted(set(zone_map) - {e.get("zone", "") for e in _T})
    if unknown:
        raise ValueError(f"zone_map 含模板未使用的区域：{unknown}")
    steps = []
    for index, sid in enumerate(template["steps"]):
        entry = next(e for e in _T if e["id"] == sid)
        step = _skeleton(entry)
        if index > 0:
            step["after"] = [template["steps"][index - 1]]
        else:
            step["after"] = []
        step["evidence"] = _remap(step["evidence"], zone_map)
        steps.append(step)
    return steps


def step_skeleton(step_id: str, zone_map: dict[str, str] | None = None) -> dict:
    """导出单个工步骨架（dsl.Step 兼容字典），供文档步骤草稿生成复用。"""
    entry = next((e for e in _T if e["id"] == step_id), None)
    if entry is None:
        raise ValueError(f"未知工步骨架：{step_id}")
    step = _skeleton(entry)
    step["evidence"] = _remap(step["evidence"], zone_map or {})
    return step


def skeleton_ids() -> list[str]:
    return [e["id"] for e in _T]


def _remap(evidence: dict, zone_map: dict[str, str]) -> dict:
    mode = next(k for k in ("all", "any", "seq") if evidence.get(k))
    conds = []
    for cond in evidence[mode]:
        (kind, body), = cond.items()
        zone = body.get("zone")
        if zone and zone.startswith(PLACEHOLDER_PREFIX):
            raw = zone[len(PLACEHOLDER_PREFIX):]
            body = {**body, "zone": zone_map.get(raw, zone)}
        conds.append({kind: body})
    return {mode: conds}


def validate_steps(steps: list[dict]) -> list[Step]:
    """用 dsl 的真实模型校验导出结果；字段漂移会在接入方之前在这里爆炸。"""
    return [Step.model_validate(s) for s in steps]
