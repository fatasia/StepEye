"""Adapted from weihu phm_core/degradation.py and prognostics.py.
MIT License

Copyright (c) 2026 fatasia

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

"""

import numpy as np

FEATURES = ("std", "mean", "diff_rms", "p95_dev")

def cycle_features(x: np.ndarray) -> np.ndarray:
    """x 为 [采样点, 周期]；返回 [周期, 4]：标准差、均值、差分均方根、95% 偏离。"""
    x = np.asarray(x, dtype=float)
    if x.ndim != 2 or x.shape[0] < 4:
        raise ValueError("需要 [采样点>=4, 周期] 的二维数组")
    median = np.median(x, axis=0)
    return np.stack(
        [
            x.std(axis=0),
            x.mean(axis=0),
            np.sqrt(np.mean(np.diff(x, axis=0) ** 2, axis=0)),
            np.quantile(np.abs(x - median), 0.95, axis=0),
        ],
        axis=1,
    )

def reference_z(features: np.ndarray, reference_fraction: float = 0.1):
    """以最早的 reference_fraction 周期为投运参考做稳健标准化。"""
    if not 0 < reference_fraction < 1:
        raise ValueError("reference_fraction 须在 (0,1)")
    count = max(int(len(features) * reference_fraction), 20)
    if count >= len(features):
        raise ValueError("周期数不足以划分参考期")
    ref = features[:count]
    center = np.median(ref, axis=0)
    spread = 1.4826 * np.median(np.abs(ref - center), axis=0)
    # 常数特征没有可标准化的尺度，置零而不是放大数值噪声。
    floor = 1e-9 * (np.abs(center) + 1.0)
    usable = spread > floor
    z = np.zeros_like(features, dtype=float)
    z[:, usable] = (features[:, usable] - center[usable]) / spread[usable]
    return z, usable, count

def persistent_crossing(index, threshold, reference_count, hold=100):
    """参考期之后首次连续 hold 个周期都高于阈值的位置，返回 (周期, 寿命占比)。"""
    above = index > threshold
    for start in range(reference_count, len(index) - hold + 1):
        if above[start : start + hold].all():
            return start, start / len(index)
    return None, None

def life_features(values):
    # 高量级、近常量传感器在 float32 方差中存在消减误差；仅聚合使用 float64。
    values = np.asarray(values, dtype="float64")
    t = np.arange(values.shape[1], dtype="float64")
    t -= t.mean()
    slope = np.einsum("btc,t->bc", values, t) / (t @ t)
    return np.concatenate(
        [values[:, -1], values.mean(1), values.std(1), slope], axis=1
    ).astype("float32")
