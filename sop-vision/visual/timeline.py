"""视频时间轴标注(V11):上传作业视频 → 抽帧 → 前端拉区间标步骤 → 三产物。

三产物:①步骤边界校验(区间是否与 DSL 步骤对齐)②区间抽帧进回归 manifest
③vlm_check 提示词样本(抽帧图 + 步骤名)。标注文件 = 简单 JSON(区间→步骤 id)。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import cv2


class TimelineStore:
    """标注项目:视频文件 + 区间标注 JSON。"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS timelines (id TEXT PRIMARY KEY, name TEXT, status TEXT,"
            " video TEXT, duration_s REAL, fps REAL, intervals TEXT, created TEXT, updated TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row) -> dict[str, Any]:
        keys = ("id", "name", "status", "video", "duration_s", "fps", "intervals", "created", "updated")
        d = dict(zip(keys, row))
        d["intervals"] = json.loads(d["intervals"] or "[]")
        return d

    def create(self, name: str, video_rel: str, duration_s: float, fps: float) -> dict[str, Any]:
        tid = f"TL-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._db.execute("INSERT INTO timelines VALUES (?,?,?,?,?,?,?,?,?)",
                             (tid, name, "draft", video_rel, duration_s, fps, "[]", now, now))
            self._db.commit()
        return self.get(tid)  # type: ignore[return-value]

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id,name,status,video,duration_s,fps,intervals,created,updated FROM timelines ORDER BY id DESC").fetchall()
        return [self._row(r) for r in rows]

    def get(self, tid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id,name,status,video,duration_s,fps,intervals,created,updated FROM timelines WHERE id=?", (tid,)).fetchone()
        return self._row(row) if row else None

    def update(self, tid: str, **fields: Any) -> dict[str, Any] | None:
        fields["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v)
        args.append(tid)
        with self._lock:
            self._db.execute(f"UPDATE timelines SET {', '.join(sets)} WHERE id=?", args)
            self._db.commit()
        return self.get(tid)


def probe_video(path: Path) -> tuple[float, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"视频无法解码: {path.name}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    return (n / fps if fps > 0 else 0.0), fps


def extract_interval_frames(video: Path, intervals: list[dict[str, Any]], out_dir: Path,
                            per_interval: int = 2) -> list[dict[str, Any]]:
    """每个区间均匀抽 per_interval 帧,按「区间步骤」命名;返回回归 manifest 行。"""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"视频无法解码: {video.name}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for iv in intervals:
        step = str(iv.get("step", "unknown"))
        t0, t1 = float(iv.get("start", 0)), float(iv.get("end", 0))
        if t1 <= t0:
            continue
        for j in range(per_interval):
            t = t0 + (t1 - t0) * (j + 0.5) / per_interval
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            name = f"tl_{step}_{int(t0 * 10):05d}_{j}.jpg"
            cv2.imwrite(str(out_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            rows.append({"file": name, "label": step, "engine": "timeline",
                         "source": video.name, "params": {"interval": [round(t0, 2), round(t1, 2)], "t": round(t, 2)}})
    cap.release()
    return rows


def check_intervals(intervals: list[dict[str, Any]], duration_s: float) -> dict[str, Any]:
    """区间自洽校验:重叠/越界/空步;返回问题清单(空=通过)。"""
    problems: list[str] = []
    sorted_iv = sorted(intervals, key=lambda x: float(x.get("start", 0)))
    for iv in sorted_iv:
        s, e = float(iv.get("start", 0)), float(iv.get("end", 0))
        if e <= s:
            problems.append(f"区间 {iv.get('step')} 结束早于开始")
        if s < 0 or e > duration_s + 0.5:
            problems.append(f"区间 {iv.get('step')} 超出视频时长({duration_s:.1f}s)")
    for a, b in zip(sorted_iv, sorted_iv[1:]):
        if float(b.get("start", 0)) < float(a.get("end", 0)) - 1e-6:
            problems.append(f"区间 {a.get('step')} 与 {b.get('step')} 重叠")
    return {"ok": not problems, "problems": problems}


def write_manifest(out_dir: Path, rows: list[dict[str, Any]]) -> Path:
    """时间轴产物 manifest,格式与 samplegen 一致;独立文件便于追溯。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    mp = out_dir / "samples.manifest.jsonl"
    with mp.open("a", encoding="utf-8") as f:
        for r in rows:
            rec = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), **r}
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return mp
