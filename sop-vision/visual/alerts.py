"""统一告警中心(V7):SOP 违规 / 常驻安全 / 拍照即检 NG 汇入一张表,状态流转 + 处理人 + 回写防重。

状态机:pending(待处理)→ processing(处理中)→ done(已处理)| false_alarm(误报)。
误报复核结果直接回流评测(误报即样本,ROADMAP R6 的入口)。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

STATUSES = ("pending", "processing", "done", "false_alarm")


class AlertStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, station TEXT,"
            " source TEXT, kind TEXT, severity TEXT, message TEXT, ref_id TEXT, status TEXT DEFAULT 'pending',"
            " handler TEXT, comment TEXT, created TEXT, updated TEXT, pushed INTEGER DEFAULT 0)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, created)")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_dict(row) -> dict[str, Any]:
        keys = ("id", "station", "source", "kind", "severity", "message", "ref_id", "status",
                "handler", "comment", "created", "updated", "pushed")
        return dict(zip(keys, row))

    def create(self, station: str, source: str, kind: str, severity: str, message: str,
               ref_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """创建告警;同 (source, kind, ref_id) 已有未处理告警时回写防重(更新时间,不新建)。

        返回 (告警记录, 是否新建)——调用方只在新建时推送,防重复打扰。
        注意:锁内只做查询/写入,字典组装在锁外(get 也要抢锁,锁内调用会自锁死锁)。
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            if ref_id:
                row = self._db.execute(
                    "SELECT id FROM alerts WHERE source=? AND kind=? AND ref_id=? AND status IN ('pending','processing')",
                    (source, kind, ref_id)).fetchone()
                if row:
                    self._db.execute("UPDATE alerts SET updated=? WHERE id=?", (now, row[0]))
                    self._db.commit()
                    got = self._db.execute(
                        "SELECT id, station, source, kind, severity, message, ref_id, status, handler, comment,"
                        " created, updated, pushed FROM alerts WHERE id=?", (row[0],)).fetchone()
                    return self._row_dict(got), False
            cur = self._db.execute(
                "INSERT INTO alerts (station, source, kind, severity, message, ref_id, status, created, updated)"
                " VALUES (?,?,?,?,?,?,'pending',?,?)",
                (station, source, kind, severity, message, ref_id, now, now))
            self._db.commit()
            got = self._db.execute(
                "SELECT id, station, source, kind, severity, message, ref_id, status, handler, comment,"
                " created, updated, pushed FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._row_dict(got), True

    def get(self, alert_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, station, source, kind, severity, message, ref_id, status, handler, comment,"
                " created, updated, pushed FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if row is None:
            return None
        keys = ("id", "station", "source", "kind", "severity", "message", "ref_id", "status",
                "handler", "comment", "created", "updated", "pushed")
        return dict(zip(keys, row))

    def list(self, status: str | None = None, source: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = "SELECT id, station, source, kind, severity, message, ref_id, status, handler, comment, created, updated, pushed FROM alerts"
        conds, args = [], []
        if status:
            conds.append("status=?")
            args.append(status)
        if source:
            conds.append("source=?")
            args.append(source)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        keys = ("id", "station", "source", "kind", "severity", "message", "ref_id", "status",
                "handler", "comment", "created", "updated", "pushed")
        return [dict(zip(keys, r)) for r in rows]

    def update(self, alert_id: int, status: str | None = None, comment: str | None = None,
               handler: str | None = None) -> dict[str, Any] | None:
        if status and status not in STATUSES:
            raise ValueError(f"状态必须是 {STATUSES} 之一")
        sets, args = [], []
        if status:
            sets.append("status=?")
            args.append(status)
        if comment is not None:
            sets.append("comment=?")
            args.append(comment)
        if handler is not None:
            sets.append("handler=?")
            args.append(handler)
        if not sets:
            return self.get(alert_id)
        sets.append("updated=?")
        args.append(time.strftime("%Y-%m-%dT%H:%M:%S"))
        args.append(alert_id)
        with self._lock:
            self._db.execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id=?", args)
            self._db.commit()
            got = self._db.execute(
                "SELECT id, station, source, kind, severity, message, ref_id, status, handler, comment,"
                " created, updated, pushed FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return self._row_dict(got) if got else None

    def mark_pushed(self, alert_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE alerts SET pushed=1 WHERE id=?", (alert_id,))
            self._db.commit()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status").fetchall()
        by = {s: n for s, n in rows}
        return {"total": sum(by.values()), "pending": by.get("pending", 0),
                "processing": by.get("processing", 0), "done": by.get("done", 0),
                "false_alarm": by.get("false_alarm", 0)}
