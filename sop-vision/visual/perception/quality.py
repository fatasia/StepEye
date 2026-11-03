"""固定参考的画质监测：曝光/局部饱和、背景光照漂移与相机位移。

异常持续多久都不自动更新参考。背景不可用或位移相关不可信时报告 unknown，
不能把“没法判断”当作正常。所有处理只用于质量门禁，不修改送入判定器的图像。
阈值需按目标相机/工位标定；离线测试不等于现场验收。
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..dsl import QualityCfg
from ..events import QUALITY, Event
from .base import Context

CONFIRM = 3
MIN_RESPONSE = 0.5
UNKNOWN_REASONS = {"invalid_frame", "background_unavailable", "texture_insufficient",
                   "reference_unavailable", "reference_shape_changed", "reference_occluded", "motion_unreliable"}


class QualityPerceiver:
    def __init__(self, cfg: QualityCfg):
        self.cfg = cfg
        self._n = 0
        self._ref: np.ndarray | None = None
        self._win: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._light_ref: float | None = None
        self._manual_required = False
        self._reference_source: str | None = None
        self._force_event = False
        self.degraded = False
        self.reason = ""
        self._cand = ""
        self._cand_n = 0
        self.metrics: dict[str, Any] = {"observation": "unknown" if cfg.enabled else "disabled", "sample_ts": None}

    def _option(self, name: str, default: Any) -> Any:
        # 兼容旧 YAML；新字段由 QualityCfg 校验后提供。
        return getattr(self.cfg, name, default)

    def snapshot(self) -> dict[str, Any]:
        return {**self.metrics, "degraded": self.degraded, "reason": self.reason,
                "candidate_reason": self._cand, "candidate_count": self._cand_n,
                "reference_ready": self._ref is not None, "reference_source": self._reference_source}

    def invalidate(self, reason: str, ts: float) -> None:
        """断流使旧的健康观测失效；保留参考，重新走健康恢复窗口。"""
        self.degraded, self.reason = True, reason
        self._cand, self._cand_n = "", 0
        self.metrics.update(observation="unknown", raw_reason=reason, sample_ts=None, invalidated_at=float(ts))

    @staticmethod
    def _small(frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scale = 160 / gray.shape[1]
        return cv2.resize(gray, (160, max(2, int(gray.shape[0] * scale))), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _valid_frame(frame: np.ndarray) -> bool:
        return (isinstance(frame, np.ndarray) and frame.dtype == np.uint8 and frame.ndim == 3
                and frame.shape[2] == 3 and min(frame.shape[:2]) >= 2)

    def _background_mask(self, frame: np.ndarray, ctx: Context) -> np.ndarray:
        sh, sw = self._small(frame).shape
        mask = np.ones((sh, sw), np.uint8)
        scale = np.array([sw / frame.shape[1], sh / frame.shape[0]])
        for geom in ctx.geoms.values():
            points = np.round(geom.poly * scale).astype(np.int32)
            excluded = np.zeros_like(mask)
            cv2.fillPoly(excluded, [points], 1)
            excluded = cv2.dilate(excluded, np.ones((7, 7), np.uint8))
            mask[excluded > 0] = 0
        return mask.astype(bool)

    @staticmethod
    def _without_foreground(mask: np.ndarray, frame: np.ndarray, ctx: Context) -> np.ndarray:
        out = mask.copy()
        sx, sy = out.shape[1] / frame.shape[1], out.shape[0] / frame.shape[0]
        boxes = [*ctx.hand_boxes, *ctx.person_boxes, *(box for _, box in ctx.object_boxes)]
        for x0, y0, x1, y1 in boxes:
            out[max(0, int(y0 * sy) - 2):max(0, int(np.ceil(y1 * sy)) + 2),
                max(0, int(x0 * sx) - 2):max(0, int(np.ceil(x1 * sx)) + 2)] = False
        return out

    def _tiles(self, small: np.ndarray):
        grid = max(1, min(16, small.shape[0], small.shape[1], int(self._option("tile_grid", 4))))
        ys, xs = np.linspace(0, small.shape[0], grid + 1, dtype=int), np.linspace(0, small.shape[1], grid + 1, dtype=int)
        for row in range(grid):
            for col in range(grid):
                yield slice(ys[row], ys[row + 1]), slice(xs[col], xs[col + 1])

    def _measure(self, frame: np.ndarray, ctx: Context, *, new_reference: bool = False):
        if not self._valid_frame(frame):
            return None, None, {"observation": "unknown"}, "invalid_frame"
        small = self._small(frame)
        if not new_reference and self._ref is not None and self._ref.shape != small.shape:
            return None, None, {"observation": "unknown"}, "reference_shape_changed"
        base_mask = self._background_mask(frame, ctx) if new_reference or self._mask is None else self._mask
        mask = self._without_foreground(base_mask, frame, ctx)
        dark, bright = int(self._option("dark_pixel", 8)), int(self._option("bright_pixel", 247))
        all_tiles = [small[y, x] for y, x in self._tiles(small)]
        pixels = int(mask.sum())
        metrics = {
            "mean": float(small.mean()), "blur_score": float(cv2.Laplacian(small, cv2.CV_64F).var()),
            "dark_fraction": float(np.mean(small <= dark)), "bright_fraction": float(np.mean(small >= bright)),
            "max_tile_dark_fraction": max(float(np.mean(tile <= dark)) for tile in all_tiles),
            "max_tile_bright_fraction": max(float(np.mean(tile >= bright)) for tile in all_tiles),
            "background_pixels": pixels, "background_fraction": float(pixels / small.size),
            "background_mean": float(small[mask].mean()) if pixels else None,
            "background_texture": float(np.std(cv2.GaussianBlur(small.astype(np.float32), (0, 0), 1.5)[mask])) if pixels else None,
            "reference_mean": self._light_ref, "brightness_drift": None, "max_tile_drift": None,
            "drifted_tile_fraction": None, "shift_px": None, "shift_response": None,
            "foreground_excluded": int(base_mask.sum() - pixels),
        }
        return small, mask, metrics, ""

    def _absolute_reason(self, metrics: dict[str, Any]) -> str:
        if self.cfg.brightness is not None and not (self.cfg.brightness[0] <= metrics["mean"] <= self.cfg.brightness[1]):
            return "exposure"
        ratio = float(self._option("recovery_ratio", 0.8)) if self.degraded and self.reason.startswith("exposure") else 1.0
        for key, option, default in (("dark_fraction", "max_dark_fraction", .35),
                                     ("bright_fraction", "max_bright_fraction", .25),
                                     ("max_tile_dark_fraction", "max_tile_dark_fraction", .8),
                                     ("max_tile_bright_fraction", "max_tile_bright_fraction", .8)):
            limit = self._option(option, default)
            if limit is not None and metrics[key] > float(limit) * ratio:
                return "exposure_local" if key.startswith("max_tile") else "exposure"
        if self.cfg.blur_min is not None and metrics["blur_score"] < self.cfg.blur_min:
            return "blur"
        return ""

    def _background_reason(self, small: np.ndarray, mask: np.ndarray, metrics: dict[str, Any]) -> str:
        if self.cfg.lighting_drift is None and self.cfg.max_shift_px is None:
            return ""
        required = max(int(self._option("min_background_pixels", 64)),
                       int(np.ceil(small.size * float(self._option("min_background_fraction", .05)))))
        if metrics["background_pixels"] < required:
            return "background_unavailable"
        if self.cfg.max_shift_px is not None and metrics["background_texture"] < float(self._option("background_texture_min", 1.0)):
            return "texture_insufficient"
        return ""

    def _capture(self, small: np.ndarray, mask: np.ndarray, source: str) -> None:
        self._mask = mask.copy()
        self._ref = small.astype(np.float32).copy()
        self._light_ref = float(self._ref[mask].mean())
        self._win = cv2.createHanningWindow(small.shape[::-1], cv2.CV_32F)
        self._reference_source = source
        self._manual_required = False
        self._cand, self._cand_n = "", 0

    def set_reference(self, frame: np.ndarray, ctx: Context | None = None) -> bool:
        """人工确认当前环境后重标定；不接受看不清/背景不可用的参考，不立即清除已确认降级。"""
        small, mask, metrics, reason = self._measure(frame, ctx or Context({}), new_reference=True)
        if not reason:
            reason = self._absolute_reason(metrics) or self._background_reason(small, mask, metrics)
        if not reason and not np.any(mask):
            reason = "background_unavailable"
        if not reason and metrics["foreground_excluded"]:
            reason = "reference_occluded"
        if reason:
            self.metrics["reference_rejected"] = reason
            return False
        self._capture(small, mask, "manual")
        self.metrics.pop("reference_rejected", None)
        return True

    def reset_reference(self) -> None:
        """清空参考后必须人工 set_reference，不能由下一张异常帧自动洗白。"""
        self._ref = self._mask = self._win = None
        self._light_ref = self._reference_source = None
        self._manual_required = True
        self._cand, self._cand_n = "", 0
        self.degraded, self.reason = True, "reference_unavailable"
        self.metrics = {"observation": "unknown", "raw_reason": self.reason, "sample_ts": None}
        self._force_event = True

    def _raw_reason(self, frame: np.ndarray, ctx: Context) -> str:
        small, mask, metrics, reason = self._measure(frame, ctx)
        self.metrics = metrics
        if reason:
            return reason
        reason = self._absolute_reason(metrics) or self._background_reason(small, mask, metrics)
        if reason:
            return reason
        needs_reference = self.cfg.lighting_drift is not None or self.cfg.max_shift_px is not None
        if not needs_reference:
            return ""
        if self._manual_required:
            return "reference_unavailable"
        if self._ref is None:
            if metrics["foreground_excluded"]:
                return "reference_occluded"
            self._capture(small, mask, "initial")
            self.metrics["reference_mean"] = self._light_ref
            return ""
        ref_mean, cur_mean = float(self._ref[mask].mean()), float(small[mask].mean())
        drift = abs(cur_mean - ref_mean) / max(ref_mean, 16.0)
        self.metrics["brightness_drift"] = float(drift)
        ratio = float(self._option("recovery_ratio", .8)) if self.degraded and self.reason.startswith("lighting") else 1.0
        if self.cfg.lighting_drift is not None:
            tile_drifts = []
            tile_limit = self._option("tile_lighting_drift", .45)
            for y, x in self._tiles(small):
                valid = mask[y, x]
                if int(valid.sum()) < 8:
                    continue
                before = float(self._ref[y, x][valid].mean())
                after = float(small[y, x][valid].mean())
                tile_drifts.append(abs(after - before) / max(before, 16.0))
            fraction = sum(v > float(tile_limit) * ratio for v in tile_drifts) / len(tile_drifts) if tile_limit is not None and tile_drifts else 0.0
            self.metrics.update(max_tile_drift=float(max(tile_drifts, default=0)), drifted_tile_fraction=float(fraction))
            if drift > self.cfg.lighting_drift * ratio:
                return "lighting_drift"
            if tile_limit is not None and fraction > float(self._option("max_drifted_tile_fraction", .15)):
                return "lighting_local"
        if self.cfg.max_shift_px is not None:
            # 使用相同的有效背景，去掉亮度直流项；输入 copy 避免 OpenCV 原地窗口操作污染参考。
            reference = np.where(mask, cv2.GaussianBlur(self._ref, (0, 0), 1.5) - ref_mean, 0).astype(np.float32)
            current = np.where(mask, cv2.GaussianBlur(small.astype(np.float32), (0, 0), 1.5) - cur_mean, 0).astype(np.float32)
            (dx, dy), response = cv2.phaseCorrelate(reference.copy(), current.copy(), self._win)
            shift = float(np.hypot(dx, dy) * frame.shape[1] / small.shape[1])
            self.metrics.update(shift_px=shift if np.isfinite(shift) else None,
                                shift_response=float(response) if np.isfinite(response) else None)
            if not np.isfinite(response) or not np.isfinite(shift) or response < MIN_RESPONSE:
                return "motion_unreliable"
            if shift > self.cfg.max_shift_px:
                return "camera_shift"
        return ""

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        self._n += 1
        if not self.cfg.enabled:
            self.metrics = {"observation": "disabled", "sample_ts": None}
            return []
        if self._n % max(1, self.cfg.every_n):
            return []
        reason = self._raw_reason(frame, ctx)
        self.metrics.update(sample_ts=float(ts), raw_reason=reason,
                            observation="unknown" if reason in UNKNOWN_REASONS else "bad" if reason else "ok")
        if reason == self.reason:
            self._cand, self._cand_n = "", 0
            if self._force_event:
                self._force_event = False
                return [Event(ts, QUALITY, state="degraded", value=reason, source="quality")]
            return []
        if reason != self._cand:
            self._cand, self._cand_n = reason, 1
        else:
            self._cand_n += 1
        needed = int(self._option("bad_confirm", CONFIRM) if reason else self._option("good_confirm", 5))
        if self._cand_n < max(1, needed):
            return []
        self.reason, self.degraded = reason, bool(reason)
        self._force_event = False
        self._cand, self._cand_n = "", 0
        return [Event(ts, QUALITY, state="degraded" if self.degraded else "ok", value=reason, source="quality")]
