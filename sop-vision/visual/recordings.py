"""Monibuca MP4 调阅：原生共享目录，按默认文件名时间索引，平台鉴权后供 Web 播放。"""
from __future__ import annotations

import re
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2

RECORD_TZ = timezone(timedelta(hours=8))


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=RECORD_TZ) if dt.tzinfo is None else dt


class RecordingIndex:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._cache: dict[tuple[str, int, int], float] = {}

    def path(self, stream: str, name: str) -> Path:
        stem = stream.replace("/", "_")
        if not re.fullmatch(re.escape(stem) + r"_\d{8}_\d{6}\.mp4", name):
            raise ValueError("录像不属于所选流或文件名无效")
        path = (self.root / name).resolve()
        if path.parent != self.root or not path.is_file():
            raise FileNotFoundError("录像不存在")
        return path

    def list(self, stream: str, start: datetime, end: datetime, limit: int = 200) -> dict:
        if end <= start or (end - start).total_seconds() > 31 * 86400:
            raise ValueError("检索结束须晚于开始，单次最多 31 天")
        if not self.root.is_dir():
            raise FileNotFoundError("录像目录不存在，请检查 MONIBUCA_RECORD_DIR")
        stem = stream.replace("/", "_")
        rows, invalid, scanned = [], 0, 0
        # 默认平铺命名模板；不递归、不读取任意浏览器路径。
        files = sorted(self.root.glob(stem + "_????????_??????.mp4"), reverse=True)
        for candidate in files:
            scanned += 1
            try:
                path = self.path(stream, candidate.name)
                stamp = candidate.stem[-15:]
                began = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=RECORD_TZ)
                if began >= end or began < start - timedelta(days=1):
                    continue  # 模板单段最长 1 小时；保留 1 天重叠裕量
                stat = path.stat()
                key = (path.name, stat.st_size, stat.st_mtime_ns)
                duration = self._cache.get(key)
                if duration is None:
                    cap = cv2.VideoCapture(str(path))
                    try:
                        fps, frames = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
                        duration = frames / fps if fps > 0 else 0
                    finally:
                        cap.release()
                    if not math.isfinite(duration) or duration <= 0:
                        invalid += 1; continue  # 未 finalize 或损坏文件不伪装成可回放
                    if len(self._cache) >= 2000:
                        self._cache.clear()
                    self._cache[key] = duration
                finished = began + timedelta(seconds=duration)
                if finished <= start:
                    continue
                rows.append({"name": path.name, "stream": stream, "start": began.isoformat(),
                             "end": finished.isoformat(), "duration_s": round(duration, 2), "bytes": stat.st_size})
                if len(rows) >= limit:
                    break
            except (ValueError, FileNotFoundError):
                invalid += 1
        return {"rows": rows, "invalid": invalid, "truncated": len(rows) >= limit and scanned < len(files),
                "timezone": "UTC+08:00"}
