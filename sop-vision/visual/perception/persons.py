"""人员感知:MediaPipe PoseLandmarker(Apache-2.0)-> 区域级人数与进/出事件(含去抖)。

与手部感知同构;S16 无人值守 / S17 人员聚集的条件来源。
计数语义:区域人数需连续 N 帧稳定才更新稳定值,并在 0↔>0 边沿发 person_enter / person_exit;
每次稳定计数变化都发 person_count(value=人数)。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..dsl import PersonsCfg
from ..events import PERSON_COUNT, PERSON_ENTER, PERSON_EXIT, Event
from .base import Context, ZoneGeom


class PersonZoneTracker:
    """把每帧的人员框转成区域级边沿与计数事件;计数变化各自需要连续 N 帧稳定。"""

    def __init__(self, geoms: dict[str, ZoneGeom], enter_frames: int = 2, exit_frames: int = 4,
                 source: str = "persons"):
        self.geoms = geoms
        self.enter_frames, self.exit_frames = enter_frames, exit_frames
        self.source = source
        self.count = {z: 0 for z in geoms}  # 稳定计数
        self._pending: dict[str, int | None] = {z: None for z in geoms}
        self._frames = {z: 0 for z in geoms}

    def update(self, boxes: list[tuple[float, float, float, float]], ts: float) -> list[Event]:
        events: list[Event] = []
        for zid, g in self.geoms.items():
            n = 0
            for x0, y0, x1, y1 in boxes:  # 中心点落在区域内即计入
                if g.contains((x0 + x1) / 2, (y0 + y1) / 2):
                    n += 1
            if n == self._pending[zid]:
                self._frames[zid] += 1
            else:
                self._pending[zid], self._frames[zid] = n, 1
            need = self.enter_frames if n > self.count[zid] else self.exit_frames
            if self._frames[zid] >= need and n != self.count[zid]:
                prev = self.count[zid]
                self.count[zid] = n
                if prev == 0 and n > 0:
                    events.append(Event(ts, PERSON_ENTER, zone=zid, source=self.source))
                elif prev > 0 and n == 0:
                    events.append(Event(ts, PERSON_EXIT, zone=zid, source=self.source))
                events.append(Event(ts, PERSON_COUNT, zone=zid, value=n, source=self.source))
        return events


class MediaPipePosePersons:
    def __init__(self, cfg: PersonsCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
        import mediapipe as mp
        from mediapipe.tasks.python import vision

        model = Path(cfg.model)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(
                f"人员模型不存在: {model}（可从 mediapipe-models 下载 pose_landmarker_lite.task）")
        self._mp = mp
        opts = vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=cfg.max_poses,
            min_pose_detection_confidence=cfg.min_conf,
            min_pose_presence_confidence=cfg.min_conf,
            min_tracking_confidence=cfg.min_conf,
        )
        self._lm = vision.PoseLandmarker.create_from_options(opts)
        self._last_ms = -1
        self.tracker = PersonZoneTracker(geoms, cfg.enter_frames, cfg.exit_frames)

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        h, w = frame.shape[:2]
        ms = max(int(ts * 1000), self._last_ms + 1)  # MediaPipe 要求时间戳严格递增
        self._last_ms = ms
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self._lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ms)
        boxes: list[tuple[float, float, float, float]] = []
        for pose in res.pose_landmarks:
            xs = [p.x * w for p in pose]
            ys = [p.y * h for p in pose]
            padx, pady = (max(xs) - min(xs)) * 0.06, (max(ys) - min(ys)) * 0.06
            box = (max(0, min(xs) - padx), max(0, min(ys) - pady),
                   min(w, max(xs) + padx), min(h, max(ys) + pady))
            boxes.append(box)
            ctx.person_boxes.append(box)
        return self.tracker.update(boxes, ts)

    def close(self) -> None:
        self._lm.close()
