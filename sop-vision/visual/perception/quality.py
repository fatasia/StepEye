"""画质自检：模糊 / 过曝欠曝 / 相机位移。看不清时发 degraded 事件，让引擎降级为“待复核”而不是误判。

相机位移只在“作业区域之外的静止背景”上估计，且手出现时跳过；劣化/恢复都需要连续多次确认，避免工件进出造成抖动。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..dsl import QualityCfg
from ..events import QUALITY, Event
from .base import Context

CONFIRM = 3
MIN_RESPONSE = 0.5  # 相位相关峰值低于此值视为不可信


class QualityPerceiver:
    def __init__(self, cfg: QualityCfg):
        self.cfg = cfg
        self._n = 0
        self._ref: np.ndarray | None = None
        self._win: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self.degraded = False
        self.reason = ""
        self._cand = ""
        self._cand_n = 0

    @staticmethod
    def _small(frame: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        s = 160 / g.shape[1]
        return cv2.resize(g, (160, max(1, int(g.shape[0] * s))), interpolation=cv2.INTER_AREA)

    def _background_mask(self, frame: np.ndarray, ctx: Context) -> np.ndarray:
        sh, sw = self._small(frame).shape
        m = np.ones((sh, sw), np.float32)
        sx, sy = sw / frame.shape[1], sh / frame.shape[0]
        for g in ctx.geoms.values():
            x0, y0, x1, y1 = g.bbox
            m[max(0, int(y0 * sy) - 3):int(y1 * sy) + 3, max(0, int(x0 * sx) - 3):int(x1 * sx) + 3] = 0
        return m

    def set_reference(self, frame: np.ndarray, ctx: Context | None = None) -> None:
        small = self._smooth(frame)
        self._mask = self._background_mask(frame, ctx) if ctx else np.ones_like(small)
        self._ref = small * self._mask
        self._win = cv2.createHanningWindow(small.shape[::-1], cv2.CV_32F)

    def _smooth(self, frame: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(self._small(frame).astype(np.float32), (0, 0), 1.5)

    def _raw_reason(self, frame: np.ndarray, ctx: Context) -> str | None:
        """返回劣化原因；"" 表示正常；None 表示本次无法判断（沿用上一状态）。"""
        c = self.cfg
        small = self._small(frame)
        if c.blur_min is not None and cv2.Laplacian(small, cv2.CV_64F).var() < c.blur_min:
            return "blur"
        if c.brightness is not None and not (c.brightness[0] <= float(small.mean()) <= c.brightness[1]):
            return "exposure"
        if c.max_shift_px is not None:
            if ctx.hand_boxes:
                return None
            if self._ref is None:
                self.set_reference(frame, ctx)
                return ""
            (dx, dy), resp = cv2.phaseCorrelate(self._ref, self._smooth(frame) * self._mask, self._win)
            if resp < MIN_RESPONSE:
                return None  # 背景纹理不足或被遮挡：相关不可信，不下结论
            if np.hypot(dx, dy) * frame.shape[1] / small.shape[1] > c.max_shift_px:
                return "camera_shift"
        return ""

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        c = self.cfg
        self._n += 1
        if not c.enabled or self._n % c.every_n:
            return []
        reason = self._raw_reason(frame, ctx)
        if reason is None or reason == self.reason:
            self._cand, self._cand_n = "", 0
            return []
        if reason != self._cand:
            self._cand, self._cand_n = reason, 1
        else:
            self._cand_n += 1
        if self._cand_n < CONFIRM:
            return []
        self.reason, self.degraded = reason, bool(reason)
        self._cand, self._cand_n = "", 0
        return [Event(ts, QUALITY, state="degraded" if self.degraded else "ok", value=reason, source="quality")]
