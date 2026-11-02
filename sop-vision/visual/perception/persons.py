"""人员感知:MediaPipe PoseLandmarker(Apache-2.0)-> 区域级人数与进/出事件(含去抖)。

与手部感知同构;S16 无人值守 / S17 人员聚集 / S4 跌倒(躯干倾角规则,免训练)的条件来源。
计数语义:区域人数需连续 N 帧稳定才更新稳定值,并在 0↔>0 边沿发 person_enter / person_exit;
每次稳定计数变化都发 person_count(value=人数)。
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from ..dsl import PersonsCfg
from ..events import PERSON_COUNT, PERSON_ENTER, PERSON_EXIT, PERSON_FALL, Event
from .base import Context, ZoneGeom

TORSO_FALL_DEG = 50.0  # 躯干与竖直方向夹角阈值:站立/弯腰 <30°,躺地 ≈90°


def is_fall_posture(pose_norm: list) -> bool:
    """倒地姿态判定(纯函数,离线可测):髋中点→肩中点连线与竖直方向夹角 ≥50°。

    pose_norm: 归一化关键点列表(33 点);侧身行走躯干仍近竖直,不会误报。
    """
    if len(pose_norm) < 25:
        return False
    ls, rs, lh, rh = pose_norm[11], pose_norm[12], pose_norm[23], pose_norm[24]
    smy, hmy = (ls[1] + rs[1]) / 2, (lh[1] + rh[1]) / 2
    dx = abs((ls[0] + rs[0]) / 2 - (lh[0] + rh[0]) / 2)
    dy = abs(smy - hmy)  # 图像 y 向下:站立时肩在髋上方,dy 大
    if dy < 1e-6:
        return True  # 躯干完全水平
    return math.degrees(math.atan2(dx, dy)) >= TORSO_FALL_DEG


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
        self.fall_enabled = cfg.fall_enabled
        self._fall_state: dict[str, tuple[bool, int, bool]] = {}  # zone -> (候选值, 稳定帧数, 当前状态)

    def _fall_events(self, poses_norm: list[list], ts: float) -> list[Event]:
        """区域级跌倒去抖:任一倒地姿态者落在区稳定 N 帧 → fall=1;全区恢复稳定 N 帧 → fall=0。"""
        events: list[Event] = []
        falling_zones = set()
        for pose in poses_norm:
            for zid, g in self.geoms.items():
                if any(g.contains(p.x, p.y) for p in (pose[11], pose[12], pose[23], pose[24])) \
                        and is_fall_posture(pose):
                    falling_zones.add(zid)
        for zid in self.geoms:
            cand = zid in falling_zones
            c, n, state = self._fall_state.get(zid, (None, 0, False))
            if cand == c:
                n += 1
            else:
                c, n = cand, 1
            if n >= 3 and cand != state:
                self._fall_state[zid] = (c, n, cand)
                events.append(Event(ts, PERSON_FALL, zone=zid, value=1 if cand else 0, source="pose_fall"))
                continue
            self._fall_state[zid] = (c, n, state)
        return events

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
        events = self.tracker.update(boxes, ts)
        if self.fall_enabled:
            fall = self._fall_events(res.pose_landmarks, ts)
            events += fall
            ctx.fall_zones += [e.zone for e in fall if e.value == 1]
        return events

    def close(self) -> None:
        self._lm.close()
