"""ROI 结果状态核验（纯视觉、无需训练）。

- diff_empty：与“空区域”参考图做差，变化像素占比超过阈值 => 有物（工件/料）。
- template ：在 ROI（含少量边距）内做归一化相关匹配，与“正确装配状态”的参考图相似 => 状态成立
             （连接器已插合、螺丝在孔位、标签已贴等）。

只在“没有手遮挡该区域”的帧上更新，并用连续帧去抖；状态变化时才发事件。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..dsl import RoiStateCfg
from ..events import OBJ_STATE, Event
from .base import Context, ZoneGeom

DEFAULT_THRESHOLD = {"diff_empty": 0.30, "template": 0.70}
HYSTERESIS = {"diff_empty": 0.8, "template": -0.15}  # 退出阈值：diff 为乘数，template 为差值


def _prep(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return cv2.GaussianBlur(gray, (5, 5), 0)


class RoiStateTracker:
    def __init__(self, cfg: RoiStateCfg, geom: ZoneGeom, ref_path: Path | None = None):
        self.cfg, self.geom, self.ref_path = cfg, geom, ref_path
        self.thr = cfg.threshold if cfg.threshold is not None else DEFAULT_THRESHOLD[cfg.mode]
        self.ref: np.ndarray | None = None
        self.state: str | None = None
        self.score = 0.0
        self._cand: str | None = None
        self._cand_n = 0
        self._cand_ts = 0.0
        if ref_path is not None and ref_path.exists():
            img = cv2.imread(str(ref_path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.ref = img

    @property
    def calibrated(self) -> bool:
        return self.ref is not None

    def _crop(self, gray: np.ndarray, pad: int = 0) -> np.ndarray:
        x0, y0, x1, y1 = self.geom.bbox
        h, w = gray.shape[:2]
        return gray[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]

    def set_reference(self, frame: np.ndarray) -> None:
        self.ref = self._crop(_prep(frame)).copy()
        self.state, self._cand, self._cand_n = None, None, 0
        if self.ref_path is not None:
            self.ref_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(self.ref_path), self.ref)

    def _score(self, gray: np.ndarray) -> float:
        ref = self.ref
        assert ref is not None
        if self.cfg.mode == "diff_empty":
            cur = self._crop(gray).astype(np.float32)
            if cur.shape != ref.shape:
                return 0.0
            diff = np.abs(cur - ref.astype(np.float32)) > 25
            m = self.geom.mask
            return float(diff[m].mean()) if m.any() else 0.0
        search = self._crop(gray, self.cfg.margin_px)
        if search.shape[0] < ref.shape[0] or search.shape[1] < ref.shape[1] or ref.std() < 1.0:
            return 0.0
        res = cv2.matchTemplate(search, ref, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(res.max(), nan=0.0, posinf=0.0, neginf=0.0))

    def update(self, gray: np.ndarray, ts: float, hand_boxes: list) -> Event | None:
        if self.ref is None or any(self.geom.overlaps_box(b, pad=8) for b in hand_boxes):
            return None  # 未标定或被手遮挡：保持原状态
        self.score = self._score(gray)
        pos_state, neg_state = self.cfg.positive_state, self.cfg.negative_state
        if self.cfg.mode == "diff_empty":
            exit_thr = self.thr * HYSTERESIS["diff_empty"]
        else:
            exit_thr = self.thr + HYSTERESIS["template"]
        if self.state == pos_state:
            raw = pos_state if self.score >= exit_thr else neg_state
        else:
            raw = pos_state if self.score >= self.thr else neg_state
        if raw == self.state:
            self._cand, self._cand_n = None, 0
            return None
        if raw != self._cand:
            self._cand, self._cand_n, self._cand_ts = raw, 1, ts
        else:
            self._cand_n += 1
        if self._cand_n >= self.cfg.min_frames:
            self.state, self._cand, self._cand_n = raw, None, 0
            return Event(self._cand_ts, OBJ_STATE, zone=self.geom.id, state=raw, conf=round(self.score, 3), source="roi")
        return None


class RoiStatePerceiver:
    def __init__(self, cfgs: list[RoiStateCfg], geoms: dict[str, ZoneGeom], ref_dir: Path | None = None):
        self.trackers = {
            c.zone: RoiStateTracker(c, geoms[c.zone], None if ref_dir is None else ref_dir / f"{c.zone}.png")
            for c in cfgs
        }

    def calibrate(self, frame: np.ndarray, zones: list[str] | None = None) -> list[str]:
        done = []
        for zid, t in self.trackers.items():
            if zones is None or zid in zones:
                t.set_reference(frame)
                done.append(zid)
        return done

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        gray = _prep(frame)
        out = []
        for t in self.trackers.values():
            ev = t.update(gray, ts, ctx.hand_boxes)
            if ev:
                out.append(ev)
        return out

    def states(self) -> dict[str, dict]:
        return {z: {"state": t.state, "score": round(t.score, 3), "calibrated": t.calibrated} for z, t in self.trackers.items()}
