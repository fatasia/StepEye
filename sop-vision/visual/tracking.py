"""有界类别感知 IoU 多目标关联；不使用外观/ReID，不是 ByteTrack。"""
from __future__ import annotations

from dataclasses import dataclass
import math
from .detector_registry import Detection


@dataclass(frozen=True)
class ObjectTrack:
    track_id: int
    label: str
    xyxy: tuple[float, float, float, float]
    confidence: float
    first_seen: float
    last_seen: float
    observations: int


def iou(a, b):
    area = max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    return area / union if union > 0 else 0.


class ClassAwareIoUTracker:
    """每相机独立实例。只输出当前检测框；短缺帧保留关联候选但不制造观测。"""
    def __init__(self, *, ttl_s=1., min_iou=.3, max_tracks=256):
        if (not math.isfinite(ttl_s) or ttl_s <= 0 or not 0 < min_iou <= 1
                or type(max_tracks) is not int or not 1 <= max_tracks <= 1000):
            raise ValueError("无效跟踪配置")
        self.ttl_s, self.min_iou, self.max_tracks = ttl_s, min_iou, max_tracks
        self._next_id = 1
        self.reset()

    def reset(self):
        # 同一实例断流后不复用旧 ID，消费者不会误串到上一段轨迹。
        self._tracks = {}
        self._last_ts = None

    def update(self, detections: list[Detection], ts: float) -> list[ObjectTrack]:
        if not math.isfinite(ts):
            raise ValueError("跟踪时间必须有限")
        if self._last_ts is not None:
            if ts == self._last_ts:
                return []  # 重复帧不能增加观察次数或输出陈旧证据。
            if ts < self._last_ts:
                self.reset()
        self._last_ts = ts
        self._tracks = {k: v for k, v in self._tracks.items() if ts - v.last_seen <= self.ttl_s}
        detections = sorted(detections, key=lambda d: (-d.confidence, d.label, d.xyxy))[:self.max_tracks]
        pairs = []
        for tid, track in self._tracks.items():
            for j, det in enumerate(detections):
                if track.label == det.label:
                    overlap = iou(track.xyxy, det.xyxy)
                    if overlap >= self.min_iou:
                        pairs.append((-overlap, tid, j))
        matches, used_tracks = {}, set()
        for _, tid, j in sorted(pairs):
            if tid not in used_tracks and j not in matches:
                matches[j] = tid
                used_tracks.add(tid)
        current = []
        for j, det in enumerate(detections):
            tid = matches.get(j)
            prev = self._tracks.get(tid)
            if prev is None:
                tid = self._next_id
                self._next_id += 1
            track = ObjectTrack(tid, det.label, det.xyxy, det.confidence,
                                prev.first_seen if prev else ts, ts,
                                prev.observations + 1 if prev else 1)
            current.append(track)
        # 当前观测优先，剩余容量保留最近丢失的候选。
        active = {t.track_id: t for t in current}
        for track in sorted(self._tracks.values(), key=lambda t: (-t.last_seen, t.track_id)):
            if len(active) >= self.max_tracks:
                break
            if track.track_id not in active:
                active[track.track_id] = track
        self._tracks = active
        return current
