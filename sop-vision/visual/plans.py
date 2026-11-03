"""厂区平面图(轻量 GIS):图片底图 + 摄像头/工位/传感器打点。

- 底图:上传 PNG/JPG 图片(厂区平面图/ CAD 导出图,由用户准备)。
- 打点:平面图内归一化坐标 (0~1),与 SOP zones 同一约定;告警产生时对应点在前端闪红。
- 伪 3D:前端 CSS perspective 实现(纯样式,零依赖)。
- 室外经纬度坐标走 geo.py(GB28181/ONVIF 目录同步或手动);本模块只管平面图。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}


class PlanStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, name TEXT,"
                         " image TEXT, created TEXT, updated TEXT)")
        self._db.execute("CREATE TABLE IF NOT EXISTS plan_points (id TEXT PRIMARY KEY, plan_id TEXT,"
                         " name TEXT, kind TEXT, x REAL, y REAL, created TEXT)")
        columns = {r[1] for r in self._db.execute("PRAGMA table_info(plan_points)")}
        if "station_id" not in columns:
            self._db.execute("ALTER TABLE plan_points ADD COLUMN station_id TEXT NOT NULL DEFAULT ''")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_ppts ON plan_points(plan_id)")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _plan(row) -> dict[str, Any]:
        keys = ("id", "name", "image", "created", "updated")
        return dict(zip(keys, row))

    @staticmethod
    def _point(row) -> dict[str, Any]:
        keys = ("id", "plan_id", "name", "kind", "x", "y", "created", "station_id")
        return dict(zip(keys, row))

    # ---- 平面图 ----
    def create(self, name: str, image_rel: str) -> dict[str, Any]:
        pid = f"PL-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._db.execute("INSERT INTO plans VALUES (?,?,?,?,?)", (pid, name, image_rel, now, now))
            self._db.commit()
        return self.get(pid)  # type: ignore[return-value]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT id,name,image,created,updated FROM plans ORDER BY id DESC").fetchall()
        return [self._plan(r) for r in rows]

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT id,name,image,created,updated FROM plans WHERE id=?", (pid,)).fetchone()
        return self._plan(row) if row else None

    def delete(self, pid: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM plans WHERE id=?", (pid,))
            self._db.execute("DELETE FROM plan_points WHERE plan_id=?", (pid,))
            self._db.commit()
        return cur.rowcount > 0

    # ---- 点位 ----
    def add_point(self, plan_id: str, name: str, kind: str, x: float, y: float,
                  station_id: str = "") -> dict[str, Any]:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("打点坐标需在 0~1(归一化)")
        if kind not in ("camera", "station", "sensor"):
            raise ValueError("kind 必须是 camera/station/sensor")
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("点位名称需为 1~100 字")
        if self.get(plan_id) is None:
            raise ValueError("平面图不存在")
        pid = f"PT-{uuid.uuid4().hex[:8].upper()}"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._db.execute("INSERT INTO plan_points (id,plan_id,name,kind,x,y,created,station_id) VALUES (?,?,?,?,?,?,?,?)",
                             (pid, plan_id, name, kind, x, y, now, station_id.strip()))
            self._db.commit()
        return self.get_point(pid)  # type: ignore[return-value]

    def get_point(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id,plan_id,name,kind,x,y,created,station_id FROM plan_points WHERE id=?", (pid,)).fetchone()
        return self._point(row) if row else None

    def points(self, plan_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,plan_id,name,kind,x,y,created,station_id FROM plan_points WHERE plan_id=? ORDER BY created", (plan_id,)).fetchall()
        return [self._point(r) for r in rows]

    def delete_point(self, pid: str, plan_id: str | None = None) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM plan_points WHERE id=?" + (" AND plan_id=?" if plan_id else ""),
                                   (pid, plan_id) if plan_id else (pid,))
            self._db.commit()
        return cur.rowcount > 0
