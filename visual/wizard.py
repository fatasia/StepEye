"""工位上线向导(V10):文档→步骤草稿→人确认→YAML→一键上线。

单一事实源是工位 YAML:向导只是它的生成器/编辑器;项目状态 draft→verified→live。
LLM 编译的步骤必须人工确认(POST steps 覆盖),部署走 manager.add_station 热加岗,无需重启。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from .llm_provider import VLMProvider

STATUSES = ("draft", "verified", "live")

COMPILE_PROMPT = (
    "你是工业 SOP 编译器。下面是一份作业指导书/工序说明,提取为有序步骤列表。"
    '只输出JSON:{"steps":[{"name":"步骤名(≤10字)","evidence":"hand|state|vlm",'
    '"hint":"证据怎么采(≤20字)"}]}。'
    "evidence 取值:hand=用手在区域停留判定;state=摆好状态后模板核验;vlm=视觉语义判定。"
    "步骤最多 8 个,不要杜撰文档里没有的步骤。\n\n文档内容:\n"
)


class WizardStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS wizard_projects (id TEXT PRIMARY KEY, name TEXT, status TEXT,"
            " yaml_text TEXT, assets TEXT, created TEXT, updated TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row) -> dict[str, Any]:
        keys = ("id", "name", "status", "yaml_text", "assets", "created", "updated")
        d = dict(zip(keys, row))
        d["assets"] = json.loads(d["assets"] or "[]")
        return d

    def create(self, name: str, yaml_text: str) -> dict[str, Any]:
        pid = f"W-{time.strftime('%Y%m%d%H%M%S')}"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._db.execute("INSERT INTO wizard_projects VALUES (?,?,?,?,?,?,?)",
                             (pid, name, "draft", yaml_text, "[]", now, now))
            self._db.commit()
        return self.get(pid)  # type: ignore[return-value]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,name,status,yaml_text,assets,created,updated FROM wizard_projects ORDER BY id DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id,name,status,yaml_text,assets,created,updated FROM wizard_projects WHERE id=?", (pid,)).fetchone()
        return self._row(row) if row else None

    def update(self, pid: str, **fields: Any) -> dict[str, Any] | None:
        fields["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
        args.append(pid)
        with self._lock:
            self._db.execute(f"UPDATE wizard_projects SET {', '.join(sets)} WHERE id=?", args)
            self._db.commit()
        return self.get(pid)

    def add_asset(self, pid: str, asset: dict[str, Any]) -> dict[str, Any] | None:
        rec = self.get(pid)
        if rec is None:
            return None
        return self.update(pid, assets=[*rec["assets"], asset])


def skeleton_yaml(station_id: str, name: str) -> str:
    return yaml.safe_dump({
        "station": station_id, "name": name, "line": "向导工位",
        "source": {"type": "rtsp", "uri": "$RTSP", "fps": 25, "width": 1280, "height": 720},
        "zones": [{"id": "work_area", "polygon": [[0.25, 0.2], [0.9, 0.2], [0.9, 0.9], [0.25, 0.9]]}],
        "persons": {"enabled": True, "fall_enabled": False},
        "safety": {"enabled": True, "rules": [
            {"id": "unattended", "kind": "person_absent", "zone": "work_area", "min_s": 300, "severity": "warn"}]},
    }, allow_unicode=True, sort_keys=False)


def compile_steps(vlm: VLMProvider, document_text: str) -> dict[str, Any]:
    """文档 → 步骤草稿;解析失败按业务失败返回(不抛),原文留 raw 供复核。"""
    text = document_text[:6000]
    out = vlm.inspect([], COMPILE_PROMPT + text)
    if not out["ok"]:
        return {"ok": False, "steps": [], "error": f"编译失败:{out['reason']}", "raw": out.get("raw")}
    steps = []
    try:
        data = json.loads(out["raw"])
        for i, s in enumerate(data.get("steps", [])):
            steps.append({"id": f"S{i + 1}", "name": str(s.get("name", f"步骤{i + 1}"))[:20],
                          "evidence": str(s.get("evidence", "vlm")), "hint": str(s.get("hint", ""))[:40]})
    except (json.JSONDecodeError, AttributeError):
        return {"ok": False, "steps": [], "error": "编译输出不是合法步骤列表", "raw": out.get("raw")}
    return {"ok": True, "steps": steps, "error": None, "raw": out.get("raw")}


def apply_steps(yaml_text: str, steps: list[dict[str, Any]]) -> str:
    """把确认后的步骤写进 YAML(evidence→条件的最小映射;vlm 步骤用步骤名做提示词)。"""
    raw = yaml.safe_load(yaml_text) or {}
    ysteps = []
    prev = None
    for i, s in enumerate(steps):
        sid = f"S{i + 1}"
        ev = str(s.get("evidence", "vlm"))
        cond = {"hand_dwell": {"zone": "work_area", "min_ms": 300}} if ev == "hand" else \
            {"vlm_check": {"prompt": f"画面显示{str(s.get('name', sid))}已完成", "every_s": 2.0}}
        ysteps.append({"id": sid, "name": str(s.get("name", sid))[:20],
                       "evidence": {"all": [cond]}, **({"after": [prev]} if prev else {}), "timeout_s": 60})
        prev = sid
    if ysteps:
        raw["steps"] = ysteps
        raw.setdefault("cycle", {"start": {"person_present": {"zone": "work_area", "min_ms": 2000}},
                                 "end": {"person_absent": {"zone": "work_area", "min_ms": 4000}},
                                 "max_cycle_s": 900})
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
