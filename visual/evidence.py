"""证据包：关键帧 + 判定依据 + 版本 + 哈希，落盘并写入 SQLite 供追溯查询。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


class CycleStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cycles (id TEXT PRIMARY KEY, station TEXT, start_wall TEXT, end_wall TEXT,"
            " result TEXT, sn TEXT, duration_s REAL, body TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def save(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cycles VALUES (?,?,?,?,?,?,?,?)",
                (rec["cycle_id"], rec["station"], rec["start_wall"], rec["end_wall"], rec["result"], rec.get("sn"),
                 rec["duration_s"], json.dumps(rec, ensure_ascii=False)),
            )
            self._db.commit()

    def list(self, limit: int = 50, result: str | None = None, sn: str | None = None,
             station: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT body FROM cycles WHERE 1=1", []
        for col, val in (("result", result), ("sn", sn), ("station", station)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY start_wall DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, cycle_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM cycles WHERE id=?", (cycle_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def stats(self, station: str | None = None, day: str | None = None) -> dict[str, Any]:
        """day 形如 2026-11-02（本地日期），缺省统计全部历史。"""
        where, args = [], []
        if station:
            where.append("station=?")
            args.append(station)
        if day:
            where.append("substr(start_wall,1,10)=?")
            args.append(day)
        q = "SELECT result, COUNT(*), AVG(duration_s) FROM cycles" + (" WHERE " + " AND ".join(where) if where else "")
        with self._lock:
            rows = self._db.execute(q + " GROUP BY result", args).fetchall()
        total = sum(r[1] for r in rows)
        passed = sum(r[1] for r in rows if r[0] == "PASS")
        return {"total": total, "pass": passed, "pass_rate": round(passed / total, 4) if total else None,
                "avg_duration_s": round(sum(r[1] * (r[2] or 0) for r in rows) / total, 2) if total else None,
                "by_result": {r[0]: r[1] for r in rows}}

    def hourly(self, hours: int = 24, station: str | None = None) -> list[dict[str, Any]]:
        """按小时聚合（本地时间前缀，如 2026-11-02T14），最近 hours 个有数据的小时。"""
        q = ("SELECT substr(start_wall,1,13) h, result, COUNT(*), AVG(duration_s) FROM cycles"
             + (" WHERE station=?" if station else "") + " GROUP BY h, result ORDER BY h DESC")
        with self._lock:
            rows = self._db.execute(q, [station] if station else []).fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for h, res, n, avg in rows:
            b = buckets.setdefault(h, {"hour": h, "total": 0, "pass": 0, "fail": 0, "uncertain": 0, "_dur": 0.0})
            b["total"] += n
            b["_dur"] += (avg or 0) * n
            b[{"PASS": "pass", "FAIL": "fail", "UNCERTAIN": "uncertain"}.get(res, "fail")] += n
        out = sorted(buckets.values(), key=lambda b: b["hour"])[-hours:]
        for b in out:
            b["avg_duration_s"] = round(b.pop("_dur") / b["total"], 2) if b["total"] else None
        return out


class EvidenceRecorder:
    """订阅引擎事件，在步骤完成 / 违规时保存关键帧；周期结束写出带哈希的证据 JSON。"""

    def __init__(self, root: Path, store: CycleStore, frame_provider: Callable[[], np.ndarray | None],
                 model_info: dict[str, str] | None = None):
        self.root, self.store, self.frame_provider = root, store, frame_provider
        self.model_info = model_info or {}
        self._dir: Path | None = None
        self._files: list[dict[str, Any]] = []
        self._seq = 0
        self._pending: list[tuple[str, dict[str, Any]]] = []

    def on_event(self, kind: str, p: dict[str, Any]) -> None:
        """引擎在处理事件的过程中回调；此刻本帧的标注图还没画好，先排队，等 flush() 再落盘。"""
        self._pending.append((kind, p))

    def flush(self) -> None:
        """每帧画完标注后调用：保证证据帧上的状态文字与事件一致（而不是慢一帧）。"""
        pending, self._pending = self._pending, []
        for kind, p in pending:
            self._handle(kind, p)

    def _handle(self, kind: str, p: dict[str, Any]) -> None:
        if kind == "cycle_start":
            self._dir = self.root / p["cycle_id"]
            (self._dir / "frames").mkdir(parents=True, exist_ok=True)
            self._files, self._seq = [], 0
            self._snap("cycle_start", None, p["ts"])
        elif kind == "step_done":
            self._snap("step_done", p["step"], p["ts"])
        elif kind == "violation":
            self._snap(f"violation_{p['kind']}", p.get("step"), p["ts"])
        elif kind == "cycle_end":
            self._finish(p)

    def _snap(self, tag: str, step: str | None, ts: float) -> None:
        frame = self.frame_provider()
        if frame is None or self._dir is None:
            return
        self._seq += 1
        name = f"frames/{self._seq:02d}_{tag}{'_' + step if step else ''}.jpg"
        cv2.imwrite(str(self._dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        self._files.append({"file": name, "tag": tag, "step": step, "stream_ts": round(ts, 3)})

    def _finish(self, rec: dict[str, Any]) -> None:
        if self._dir is None:
            return
        self._snap("cycle_end", None, rec["ts"])
        body = {k: v for k, v in rec.items() if k != "ts"}
        body["frames"] = self._files
        body["models"] = self.model_info
        canon = json.dumps(body, sort_keys=True, ensure_ascii=False)
        body["sha256"] = hashlib.sha256(canon.encode()).hexdigest()  # 篡改可检出；上线时再叠加签名
        (self._dir / "evidence.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.save(body)
        self._dir = None
