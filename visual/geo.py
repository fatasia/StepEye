"""GIS 定位:工位/相机经纬度存储与地图聚合(Leaflet/OSM 前端,零费用)。

坐标来源:部署时在工位 YAML 里写 location: {lat, lng, label};或 API 更新。
无坐标的工位在地图页以列表呈现(不硬造坐标)。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class GeoStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS geo_points (id TEXT PRIMARY KEY, name TEXT, kind TEXT,"
            " lat REAL, lng REAL, created TEXT, updated TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row) -> dict[str, Any]:
        keys = ("id", "name", "kind", "lat", "lng", "created", "updated")
        return dict(zip(keys, row))

    def upsert(self, pid: str, name: str, kind: str, lat: float, lng: float) -> dict[str, Any]:
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError(f"坐标越界: lat={lat}, lng={lng}")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._db.execute(
                "INSERT INTO geo_points VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
                " name=excluded.name, kind=excluded.kind, lat=excluded.lat, lng=excluded.lng, updated=excluded.updated",
                (pid, name, kind, lat, lng, now, now))
            self._db.commit()
        return self.get(pid)  # type: ignore[return-value]

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id,name,kind,lat,lng,created,updated FROM geo_points WHERE id=?", (pid,)).fetchone()
        return self._row(row) if row else None

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT id,name,kind,lat,lng,created,updated FROM geo_points"
        args: list = []
        if kind:
            q += " WHERE kind=?"
            args.append(kind)
        q += " ORDER BY id"
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, pid: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM geo_points WHERE id=?", (pid,))
            self._db.commit()
        return cur.rowcount > 0


# ---- 自动坐标来源(GB28181 / ONVIF) ----
# 国标设备目录与 ONVIF GetGeoLocation 都可能带经纬度;ZLMediaKit 部署后其 HTTP API
# (如 /api/device/list)可直接拉到。这里定义统一的数据源接口,真实拉取由部署配置。

def sync_from_device_list(devices: list[dict[str, Any]], store: "GeoStore") -> list[dict[str, Any]]:
    """把设备清单(含 lat/lng,可空)同步进点位库;无坐标设备跳过并报告。

    devices 元素: {id, name, lat?, lng?, source: "gb28181"|"onvif"} —— ZLMediaKit/ONVIF
    客户端适配成此形态后调用。返回成功入库的点位。
    """
    out = []
    for d in devices:
        lat, lng = d.get("lat"), d.get("lng")
        if lat is None or lng is None:
            continue
        out.append(store.upsert(str(d["id"]), str(d.get("name", d["id"])),
                                d.get("source", "camera"), float(lat), float(lng)))
    return out
