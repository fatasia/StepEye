"""巡更到位：计划 × 区域人员观测，不识别人脸或巡更人员身份。

时段归属于开始日期，跨午夜的结束时间落在次日。时区固定 UTC+08:00，
避免 Windows 缺少 IANA tzdata 时出现不同结果。只有观测覆盖充分才报“未到”。
"""
from __future__ import annotations

import math
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

LOCAL_TZ = timezone(timedelta(hours=8), "UTC+08:00")
TIME_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class PatrolPlan(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    station: str = Field(min_length=1, max_length=160)
    zone: str = Field(min_length=1, max_length=160)
    start: str = Field(pattern=TIME_PATTERN)
    end: str = Field(pattern=TIME_PATTERN)
    min_presence_s: float = Field(default=10, ge=1, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_window(self):
        self.name, self.station, self.zone = self.name.strip(), self.station.strip(), self.zone.strip()
        if not self.name or not self.station or not self.zone:
            raise ValueError("名称、工位和区域不能为空")
        if self.start == self.end:
            raise ValueError("开始与结束时间不能相同")
        start, end = window_for(self, date(2026, 1, 1))
        if self.min_presence_s > end - start:
            raise ValueError("最短到位时长不能超过计划时段")
        return self


def window_for(plan: PatrolPlan, day: date) -> tuple[float, float]:
    start = datetime.combine(day, clock_time.fromisoformat(plan.start), LOCAL_TZ)
    end = datetime.combine(day, clock_time.fromisoformat(plan.end), LOCAL_TZ)
    if end <= start:
        end += timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, LOCAL_TZ).isoformat(timespec="seconds") if ts is not None else None


class PatrolStore:
    def __init__(self, db_path: Path, max_gap_s: float = 3.0):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS patrol_plans ("
                         "id TEXT PRIMARY KEY, name TEXT, station TEXT, zone TEXT, start TEXT, end TEXT,"
                         " min_presence_s REAL, created_at REAL, deleted_at REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS patrol_observations ("
                         "id INTEGER PRIMARY KEY AUTOINCREMENT, station TEXT, zone TEXT, at REAL, count INTEGER)")
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_patrol_samples "
                         "ON patrol_observations(station,zone,at)")
        self._db.commit()
        self.max_gap_s = max_gap_s
        self._last_sample: dict[str, float] = {}

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _plan(row) -> dict[str, Any]:
        return dict(zip(("id", "name", "station", "zone", "start", "end", "min_presence_s", "created_at", "deleted_at"), row))

    def plans(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM patrol_plans" +
                                    ("" if include_deleted else " WHERE deleted_at IS NULL") +
                                    " ORDER BY start,station,zone,name,id").fetchall()
        return [self._plan(r) for r in rows]

    def add_plan(self, plan: PatrolPlan | dict, at: float | None = None) -> dict[str, Any]:
        plan = PatrolPlan.model_validate(plan)
        created_at = time.time() if at is None else at
        pid = "PT-" + uuid.uuid4().hex[:16]
        with self._lock:
            self._db.execute("INSERT INTO patrol_plans VALUES (?,?,?,?,?,?,?,?,NULL)",
                             (pid, plan.name, plan.station, plan.zone, plan.start, plan.end,
                              plan.min_presence_s, created_at))
            self._db.commit()
        return next(p for p in self.plans() if p["id"] == pid)

    def delete_plan(self, pid: str, at: float | None = None) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE patrol_plans SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                                   (time.time() if at is None else at, pid))
            self._db.commit()
        return cur.rowcount > 0

    def observe(self, station: str, zone: str, count: int | None, at: float | None = None) -> None:
        """count=None 表示当前不可判定；无帧不调用，不延长最后一次观测。"""
        ts = time.time() if at is None else at
        if not math.isfinite(ts) or (count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0)):
            raise ValueError("观测时间须有限，人数须为非负整数或 None")
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO patrol_observations(station,zone,at,count) VALUES (?,?,?,?)",
                             (station, zone, ts, count))
            self._db.commit()

    def observe_runtime(self, runtime, at: float | None = None) -> None:
        """真实采集线程每帧调用，在本模块内每工位节流到 1Hz。

        sim/file 不作现场到岗证据；画质降级保留 unknown 样本，不把未知当无人。
        使用 MediaPipe 人员跟踪器的稳定计数，避开依赖计数变化事件的初始无人漏记。
        """
        if runtime.cfg.source.type not in ("rtsp", "camera") or runtime.persons is None:
            return
        ts = time.time() if at is None else at
        station = runtime.cfg.station
        if ts - self._last_sample.get(station, -math.inf) < 1.0:
            return
        self._last_sample[station] = ts
        zones = {p["zone"] for p in self.plans() if p["station"] == station}
        if not zones:
            return
        tracker = runtime.persons.tracker
        ready = runtime.frame_idx >= max(runtime.cfg.persons.enter_frames, runtime.cfg.persons.exit_frames)
        healthy = ready and not runtime.engine.world.quality_bad
        for zone in zones:
            self.observe(station, zone, tracker.count.get(zone) if healthy else None, ts)

    def report(self, day: str | date, now: float | None = None) -> dict[str, Any]:
        if isinstance(day, str):
            parsed = date.fromisoformat(day)
            if parsed.isoformat() != day:
                raise ValueError("日期必须为 YYYY-MM-DD")
            day = parsed
        now = time.time() if now is None else now
        rows = []
        for stored in self.plans(include_deleted=True):
            plan = PatrolPlan.model_validate(stored)
            start, end = window_for(plan, day)
            if stored["created_at"] >= end or (stored["deleted_at"] is not None and stored["deleted_at"] <= start):
                continue
            cutoff = min(end, now)
            with self._lock:
                samples = self._db.execute(
                    "SELECT id,at,count FROM patrol_observations WHERE station=? AND zone=? AND at>=? AND at<=? ORDER BY at",
                    (plan.station, plan.zone, start - self.max_gap_s, cutoff + self.max_gap_s)).fetchall()
            covered = present_s = longest = run = 0.0
            known_segments = []
            run_first = arrival = None
            evidence = None
            for left, right in zip(samples, samples[1:]):
                a, b = max(start, left[1], stored["created_at"]), min(cutoff, right[1])
                if stored["deleted_at"] is not None:
                    b = min(b, stored["deleted_at"])
                valid = b > a and 0 < right[1] - left[1] <= self.max_gap_s and left[2] is not None and right[2] is not None
                if not valid:
                    run = 0.0; run_first = None
                    continue
                covered += b - a
                known_segments.append((a, b))
                if left[2] > 0 and right[2] > 0:
                    if run_first is None:
                        run_first = (a, left[0])
                    run += b - a
                    present_s += b - a
                    longest = max(longest, run)
                    if arrival is None and run >= plan.min_presence_s:
                        arrival = run_first[0] + plan.min_presence_s
                        evidence = {"kind": "person_count_samples", "first_id": run_first[1], "last_id": right[0],
                                    "from": _iso(run_first[0]), "to": _iso(b)}
                else:
                    run = 0.0; run_first = None
            duration = end - start
            coverage = min(1.0, covered / duration)
            cursor, longest_unknown = start, 0.0
            for a, b in known_segments:
                longest_unknown = max(longest_unknown, a - cursor)
                cursor = max(cursor, b)
            longest_unknown = max(longest_unknown, end - cursor)
            if arrival is not None:
                status, reason = "arrived", "区域内人员连续停留达到要求"
            elif now < start:
                status, reason = "pending", "时段尚未开始"
            elif now < end:
                status, reason = "pending", "时段进行中，等待到位"
            elif coverage >= 0.95 and longest_unknown < plan.min_presence_s:
                status, reason = "missed", "时段结束，充分观测内未达到最短停留时长"
            else:
                status, reason = "unknown", "观测不足或离线，不能判断是否到位"
            rows.append({**stored, "day": day.isoformat(), "window_start": _iso(start), "window_end": _iso(end),
                         "status": status, "reason": reason, "coverage": round(coverage, 4),
                         "observed_s": round(covered, 2), "present_s": round(present_s, 2),
                         "longest_unknown_s": round(longest_unknown, 2),
                         "longest_presence_s": round(longest, 2), "arrived_at": _iso(arrival), "evidence": evidence})
        rows.sort(key=lambda row: (row["window_start"], row["station"], row["zone"], row["id"]))
        counts = {status: sum(r["status"] == status for r in rows) for status in ("arrived", "missed", "unknown", "pending")}
        return {"day": day.isoformat(), "timezone": "UTC+08:00", "rows": rows, "counts": counts,
                "generated_at": _iso(now), "observation_max_gap_s": self.max_gap_s,
                "note": "仅确认区域内有人，不识别巡更人员身份；未覆盖的时间不作未到结论。"}
