"""模板库:场景模板与工位模板的加载与渲染($token 参数替换)。"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "configs" / "templates"


def list_templates() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for fname, key in (("inspection-templates.yaml", "inspection"), ("station-templates.yaml", "station")):
        path = TEMPLATES_DIR / fname
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("templates") or []
        ids = [t["id"] for t in items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{fname} 模板 id 重复")
        out[key] = items
    return out


def render_station(template_id: str, params: dict[str, str]) -> str:
    """按参数渲染工位模板 YAML;缺失参数原样保留($rtsp 一眼能看出没填)。"""
    t = next((t for t in list_templates()["station"] if t["id"] == template_id), None)
    if t is None:
        raise KeyError(f"工位模板不存在: {template_id}")
    text = str(t["yaml"])
    for k, v in params.items():
        text = text.replace(f"${k}", str(v))
    return text
