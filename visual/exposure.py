"""可选的有界曝光校正；仅处理检测输入，原图不变。"""
from __future__ import annotations

import cv2
import numpy as np


def normalize_exposure(img: np.ndarray) -> tuple[np.ndarray, dict]:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    stride = max(1, max(value.shape) // 512)
    sample = value[::stride, ::stride]
    median = float(np.median(sample))
    clipped = float(np.mean((sample <= 2) | (sample >= 253)))
    if median <= 2 or median >= 253:
        gain = 1.0
    else:
        gain = float(np.clip(128.0 / median, 0.5, 2.0))
    lut = np.rint(np.arange(256, dtype=np.float32) * gain).clip(0, 255).astype(np.uint8)
    hsv[:, :, 2] = cv2.LUT(value, lut)
    corrected = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return corrected, {'median': round(median, 1), 'gain': round(gain, 3),
                       'clipped_fraction': round(clipped, 4), 'unusable': clipped >= .97}
