"""有界类别感知 IoU 多目标关联；不使用外观/ReID，不是 ByteTrack。

匹配用速度外推的预测框（轻量运动门控，MOT16 评测 docs/MOT-EVAL-2026-11-05.md
建议项）：速度取最近两次观测的位移/时间差，幅度钳制在单框对角线×每秒 2 以内，
防止跳变检测把预测甩出画面；只有一次观测或时间倒流时退化为原框匹配。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
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


def _diag(box):
    return math.hypot(box[2] - box[0], box[3] - box[1])


@dataclass
class _TrackState:
    track: ObjectTrack
    prev_box: tuple[float, float, float, float] | None = None
    prev_ts: float | None = None
    velocity: tuple[float, float, float, float] | None = None


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
        self._tracks: dict[int, _TrackState] = {}
        self._last_ts = None

    def _predicted_box(self, state: _TrackState, ts: float):
        if state.velocity is None:
            return state.track.xyxy
        dt = min(max(ts - state.track.last_seen, 0.), self.ttl_s)
        return tuple(v + c * dt for v, c in zip(state.track.xyxy, state.velocity))

    def _advanced(self, state: _TrackState, box, ts: float) -> _TrackState:
        """记录本帧观测后的速度；跳变由幅度钳制兜底，首帧或零间隔保持 None。"""
        velocity = None
        if state.prev_box is not None and state.prev_ts is not None:
            dt = ts - state.prev_ts
            if dt > 0:
                dx = (box[0] + box[2] - state.prev_box[0] - state.prev_box[2]) / (2. * dt)
                dy = (box[1] + box[3] - state.prev_box[1] - state.prev_box[3]) / (2. * dt)
                speed_cap = 2. * max(_diag(box), 1e-6)
                norm = math.hypot(dx, dy)
                if norm > speed_cap:
                    dx, dy = (c * speed_cap / norm for c in (dx, dy))
                velocity = (dx, dy, dx, dy)
        return replace(state, prev_box=state.track.xyxy, prev_ts=state.track.last_seen,
                       velocity=velocity)

    def update(self, detections: list[Detection], ts: float) -> list[ObjectTrack]:
        if not math.isfinite(ts):
            raise ValueError("跟踪时间必须有限")
        if self._last_ts is not None:
            if ts == self._last_ts:
                return []  # 重复帧不能增加观察次数或输出陈旧证据。
            if ts < self._last_ts:
                self.reset()
        self._last_ts = ts
        self._tracks = {k: v for k, v in self._tracks.items() if ts - v.track.last_seen <= self.ttl_s}
        detections = sorted(detections, key=lambda d: (-d.confidence, d.label, d.xyxy))[:self.max_tracks]
        pairs = []
        for tid, state in self._tracks.items():
            predicted = self._predicted_box(state, ts)
            for j, det in enumerate(detections):
                if state.track.label == det.label:
                    overlap = iou(predicted, det.xyxy)
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
                state = _TrackState(ObjectTrack(tid, det.label, det.xyxy, det.confidence, ts, ts, 1),
                                    prev_box=det.xyxy, prev_ts=ts)
            else:
                track = ObjectTrack(tid, det.label, det.xyxy, det.confidence,
                                    prev.track.first_seen, ts, prev.track.observations + 1)
                state = self._advanced(prev, det.xyxy, ts)
                state.track = track
            self._tracks[tid] = state
            current.append(state.track)
        # 当前观测优先，剩余容量保留最近丢失的候选。
        current_ids = {st.track_id for st in current}
        active = {tid: st for tid, st in self._tracks.items() if tid in current_ids}
        for tid, state in sorted(self._tracks.items(),
                                 key=lambda kv: (-kv[1].track.last_seen, kv[0])):
            if len(active) >= self.max_tracks:
                break
            active.setdefault(tid, state)
        self._tracks = active
        return current
