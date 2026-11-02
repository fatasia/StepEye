"""画质自检：模糊 / 过曝欠曝 / 相机位移 / 光照突变。看不清时发 degraded 事件，让引擎降级为“待复核”而不是误判。

相机位移只在“作业区域之外的静止背景”上估计，且手出现时跳过；劣化/恢复都需要连续多次确认，避免工件进出造成抖动。
光照突变在背景区域上做相对亮度漂移检测:开灯/关灯/日光变化触发降级;若持续存在(灯真的换了),慢速自适应到新光照并恢复,
短暂闪烁(叉车灯扫过/阴影)不会自适应。
"""
from __future__ import annotations

import cv2
import numpy as np

from ..dsl import QualityCfg
from ..events import QUALITY, Event
from .base import Context

CONFIRM = 3
MIN_RESPONSE = 0.5  # 相位相关峰值低于此值视为不可信
LIGHT_ADAPT_AFTER = 20  # 光照劣化连续确认次数达到后,自适应到新光照(约 4s @25fps/every_n=5)


class QualityPerceiver:
    def __init__(self, cfg: QualityCfg):
        self.cfg = cfg
        self._n = 0
        self._ref: np.ndarray | None = None
        self._win: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._light_ref: float | None = None
        self._light_bad_n = 0
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

    def _bg_mean(self, smoothed: np.ndarray) -> float:
        """背景区域平均亮度(排除作业区):光照漂移只在环境光上度量,工件进出不干扰。"""
        if self._mask is None:
            return float(smoothed.mean())
        m = float(self._mask.sum())
        return float((smoothed * self._mask).sum() / max(m, 1.0))

    def _raw_reason(self, frame: np.ndarray, ctx: Context) -> str | None:
        """返回劣化原因；"" 表示正常；None 表示本次无法判断（沿用上一状态）。"""
        c = self.cfg
        small = self._small(frame)
        if c.blur_min is not None and cv2.Laplacian(small, cv2.CV_64F).var() < c.blur_min:
            return "blur"
        if c.brightness is not None and not (c.brightness[0] <= float(small.mean()) <= c.brightness[1]):
            return "exposure"
        if c.lighting_drift is not None:
            if self._light_ref is None:
                self._light_ref = self._bg_mean(self._smooth(frame))
                return ""
            cur = self._bg_mean(self._smooth(frame))
            if abs(cur - self._light_ref) > c.lighting_drift * max(self._light_ref, 1e-3):
                return "lighting_drift"
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
        # 光照自适应计数:同状态持续帧也会走到这里(确认/保持都要累计),否则永远到不了阈值
        if reason == "lighting_drift":
            self._light_bad_n += 1
            if self.degraded and self._light_bad_n >= LIGHT_ADAPT_AFTER:  # 灯真的换了:慢速自适应,恢复判定
                self._light_ref = self._bg_mean(self._smooth(frame))
        else:
            self._light_bad_n = 0
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
