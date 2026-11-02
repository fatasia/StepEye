from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import cv2
import numpy as np

from ..dsl import Zone
from ..events import Event


class ZoneGeom:
    def __init__(self, zone: Zone, w: int, h: int):
        self.id = zone.id
        self.poly = np.array([[x * w, y * h] for x, y in zone.polygon], dtype=np.float32)
        xs, ys = self.poly[:, 0], self.poly[:, 1]
        self.bbox = (max(0, int(xs.min())), max(0, int(ys.min())), min(w, int(np.ceil(xs.max()))), min(h, int(np.ceil(ys.max()))))
        x0, y0, x1, y1 = self.bbox
        mask = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
        cv2.fillPoly(mask, [(self.poly - [x0, y0]).astype(np.int32)], 255)
        self.mask = mask > 0

    def contains(self, x: float, y: float) -> bool:
        return cv2.pointPolygonTest(self.poly, (float(x), float(y)), False) >= 0

    def overlaps_box(self, box: tuple[float, float, float, float], pad: int = 0) -> bool:
        x0, y0, x1, y1 = self.bbox
        return not (box[2] < x0 - pad or box[0] > x1 + pad or box[3] < y0 - pad or box[1] > y1 + pad)


def build_geoms(zones: list[Zone], w: int, h: int) -> dict[str, ZoneGeom]:
    return {z.id: ZoneGeom(z, w, h) for z in zones}


@dataclass
class Context:
    """同一帧内感知模块之间共享的信息（如手的位置，用于“手遮挡时不更新结果状态”）。"""
    geoms: dict[str, ZoneGeom]
    hand_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    hand_points: list[tuple[float, float]] = field(default_factory=list)
    person_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)

    def reset(self) -> None:
        self.hand_boxes.clear()
        self.hand_points.clear()
        self.person_boxes.clear()


class Perceiver(Protocol):
    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]: ...
