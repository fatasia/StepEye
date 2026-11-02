"""手部感知：MediaPipe HandLandmarker -> 手进入/离开区域事件（含去抖）。"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..dsl import HandsCfg
from ..events import HAND_ENTER, HAND_EXIT, Event
from .base import Context, ZoneGeom


class HandZoneTracker:
    """把每帧的手部关键点转成 hand_enter / hand_exit 边沿事件；进出各自需要连续 N 帧，抑制检测抖动。"""

    def __init__(self, geoms: dict[str, ZoneGeom], enter_frames: int, exit_frames: int, source: str = "hands"):
        self.geoms = geoms
        self.enter_frames, self.exit_frames = enter_frames, exit_frames
        self.source = source
        self.inside = {z: False for z in geoms}
        self._in = {z: 0 for z in geoms}
        self._out = {z: 0 for z in geoms}
        self._first_ts = {z: 0.0 for z in geoms}

    def update(self, points: list[tuple[float, float]], ts: float) -> list[Event]:
        events: list[Event] = []
        for zid, g in self.geoms.items():
            touched = any(g.contains(x, y) for x, y in points)
            if touched:
                if self._in[zid] == 0:
                    self._first_ts[zid] = ts
                self._in[zid] += 1
                self._out[zid] = 0
                if not self.inside[zid] and self._in[zid] >= self.enter_frames:
                    self.inside[zid] = True
                    events.append(Event(self._first_ts[zid], HAND_ENTER, zone=zid, source=self.source))
            else:
                self._out[zid] += 1
                self._in[zid] = 0
                if self.inside[zid] and self._out[zid] >= self.exit_frames:
                    self.inside[zid] = False
                    events.append(Event(ts, HAND_EXIT, zone=zid, source=self.source))
        return events


class MediaPipeHands:
    def __init__(self, cfg: HandsCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
        import mediapipe as mp
        from mediapipe.tasks.python import vision

        model = Path(cfg.model)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(f"手部模型不存在: {model}（可从 mediapipe-models 下载 hand_landmarker.task）")
        self._mp = mp
        opts = vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=cfg.num_hands,
            min_hand_detection_confidence=cfg.min_conf,
            min_hand_presence_confidence=cfg.min_conf,
            min_tracking_confidence=cfg.min_conf,
        )
        self._lm = vision.HandLandmarker.create_from_options(opts)
        self._points = cfg.points
        self._last_ms = -1
        self.tracker = HandZoneTracker(geoms, cfg.enter_frames, cfg.exit_frames)

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        h, w = frame.shape[:2]
        ms = max(int(ts * 1000), self._last_ms + 1)  # MediaPipe 要求时间戳严格递增
        self._last_ms = ms
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self._lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ms)
        pts: list[tuple[float, float]] = []
        for hand in res.hand_landmarks:
            xs = [p.x * w for p in hand]
            ys = [p.y * h for p in hand]
            ctx.hand_boxes.append((min(xs), min(ys), max(xs), max(ys)))
            pts += [(hand[i].x * w, hand[i].y * h) for i in self._points]
        ctx.hand_points += pts
        return self.tracker.update(pts, ts)

    def close(self) -> None:
        self._lm.close()
