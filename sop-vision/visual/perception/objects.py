"""通用目标检测感知:MediaPipe ObjectDetector(efficientdet,Apache-2.0)→ 类别×区域 计数事件。

与人员感知同构;COCO 预训练直接覆盖 person / cell phone / knife 等常见类别(S15 手机、S1 人员越界的感知底座);
烟火/PPE 等专用模型走同一接口换 .task 模型文件(训练腿产出),不改代码。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..dsl import DetectorCfg
from ..events import OBJECT_COUNT, OBJECT_ENTER, OBJECT_EXIT, Event
from .base import Context, ZoneGeom


class ZoneCountTracker:
    """key×区域 计数去抖:中心点落区计数;边沿与计数变化各自需要连续 N 帧稳定。"""

    def __init__(self, geoms: dict[str, ZoneGeom], enter_frames: int = 2, exit_frames: int = 4,
                 source: str = "detector"):
        self.geoms = geoms
        self.enter_frames, self.exit_frames = enter_frames, exit_frames
        self.source = source
        self.count: dict[tuple[str, str], int] = {}  # (key, zone) -> 稳定计数
        self._pending: dict[tuple[str, str], int | None] = {}
        self._frames: dict[tuple[str, str], int] = {}

    def update(self, hits: list[tuple[str, tuple[float, float, float, float]]], ts: float) -> list[Event]:
        """hits: [(key, box)]。返回 enter/exit/count 事件(与 PersonZoneTracker 语义一致)。"""
        events: list[Event] = []
        seen: dict[tuple[str, str], int] = {}
        for key, box in hits:
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            for zid, g in self.geoms.items():
                if g.contains(cx, cy):
                    seen[(key, zid)] = seen.get((key, zid), 0) + 1
        for zid in self.geoms:
            for key in {k for k, _ in self.count} | {k for k, _ in seen}:
                k = (key, zid)
                n = seen.get(k, 0)
                if n == self._pending.get(k):
                    self._frames[k] = self._frames.get(k, 0) + 1
                else:
                    self._pending[k], self._frames[k] = n, 1
                need = self.enter_frames if n > self.count.get(k, 0) else self.exit_frames
                if self._frames.get(k, 0) >= need and n != self.count.get(k, 0):
                    prev = self.count.get(k, 0)
                    self.count[k] = n
                    if prev == 0 and n > 0:
                        events.append(Event(ts, OBJECT_ENTER, zone=zid, name=key, source=self.source))
                    elif prev > 0 and n == 0:
                        events.append(Event(ts, OBJECT_EXIT, zone=zid, name=key, source=self.source))
                    events.append(Event(ts, OBJECT_COUNT, zone=zid, name=key, value=n, source=self.source))
        return events


class MediaPipeObjectDetector:
    def __init__(self, cfg: DetectorCfg, geoms: dict[str, ZoneGeom], base_dir: Path | None = None):
        import mediapipe as mp
        from mediapipe.tasks.python import vision

        model = Path(cfg.model)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(
                f"检测模型不存在: {model}（模型为 .tflite(如 efficientdet_lite0.tflite);"
                f"烟火/PPE 等专用模型放同路径即可,接口不变）")
        self._mp = mp
        opts = vision.ObjectDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            max_results=cfg.max_results,
            score_threshold=cfg.min_conf,
        )
        self._lm = vision.ObjectDetector.create_from_options(opts)
        self._last_ms = -1
        self.classes = tuple(cfg.classes)
        self.tracker = ZoneCountTracker(geoms, cfg.enter_frames, cfg.exit_frames)

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        ms = max(int(ts * 1000), self._last_ms + 1)  # MediaPipe 要求时间戳严格递增
        self._last_ms = ms
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = self._lm.detect_for_video(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb), ms)
        hits: list[tuple[str, tuple[float, float, float, float]]] = []
        for det in res.detections:
            cat = det.categories[0]
            if cat.category_name not in self.classes:
                continue
            b = det.bounding_box
            box = (float(b.origin_x), float(b.origin_y), float(b.origin_x + b.width), float(b.origin_y + b.height))
            ctx.object_boxes.append((cat.category_name, box))
            hits.append((cat.category_name, box))
        return self.tracker.update(hits, ts)

    def close(self) -> None:
        self._lm.close()
