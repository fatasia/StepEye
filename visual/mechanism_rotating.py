"""按需轴承包络谱分析；候选证据，不输出健康概率或寿命。"""
from __future__ import annotations

# Adapted from weihu phm_core/rotating.py, revision
# 01d77002e960b93254f137a8dd7e618abb12c3ae.
# MIT License, Copyright (c) 2026 fatasia
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import csv
import io
import math
import re
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_POINTS = 200000
MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_SPECTRUM_POINTS = 2000
SOURCE_REVISION = '01d77002e960b93254f137a8dd7e618abb12c3ae'
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Order = Annotated[Number, Field(gt=0, le=100)]


class MechanismUnavailable(RuntimeError):
    pass


class RotatingBody(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field(default='', max_length=200)
    unit: Literal['g', 'm/s2']
    sample_rate_hz: Number = Field(ge=100, le=200000)
    rpm: Number = Field(gt=0, le=60000)
    band_hz: tuple[Number, Number]
    orders: dict[str, Order] = Field(min_length=1, max_length=16)
    values: list[Number] | None = Field(default=None, min_length=100, max_length=MAX_POINTS)
    csv_text: str | None = Field(default=None, min_length=1, max_length=MAX_CSV_BYTES)

    @model_validator(mode='after')
    def validate_contract(self):
        if (self.values is None) == (self.csv_text is None):
            raise ValueError('values与csv_text必须且只能提供一个')
        if not 0 < self.band_hz[0] < self.band_hz[1] < self.sample_rate_hz / 2:
            raise ValueError('带通范围须满足0 < 下限 < 上限 < 采样率/2')
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', k) for k in self.orders):
            raise ValueError('阶次名称须为1–64位英文字母、数字、下划线或连字符')
        if max(self.orders.values()) * self.rpm / 60 * 3 >= self.sample_rate_hz / 2:
            raise ValueError('三次特征谐波必须低于奈奎斯特频率')
        return self


def import_waveform(text: str, sample_rate_hz: float) -> np.ndarray:
    """严格等间隔时间序列；不排序、补值或把缺样变成零。"""
    if len(text.encode('utf8')) > MAX_CSV_BYTES:
        raise ValueError('波形CSV限8MB UTF-8文本')
    reader = csv.reader(io.StringIO(text.lstrip('\ufeff')))
    if next(reader, None) != ['time_s', 'acceleration']:
        raise ValueError('CSV表头必须为time_s,acceleration')
    values, previous = [], None
    dt = 1 / sample_rate_hz
    for row in reader:
        if len(values) >= MAX_POINTS or len(row) != 2:
            raise ValueError('CSV限200000点且每行必须为两列')
        try:
            timestamp, value = map(float, row)
            if not math.isfinite(timestamp) or abs(timestamp) > 1e9:
                raise ValueError
            if not math.isfinite(value) or abs(value) > 1e6:
                raise ValueError
        except ValueError:
            raise ValueError('时间须为有限秒数，幅值须为绝对值不超过1e6的有限数') from None
        if previous is not None and abs(timestamp - previous - dt) > max(1e-9, dt * .01):
            raise ValueError('时间须递增且间隔与采样率一致（容差1%），不自动重采样')
        previous = timestamp
        values.append(value)
    return np.asarray(values, dtype=np.float64)


def _display_spectrum(frequencies, density):
    """完整频谱用于评分，显示谱按分组最大值保峰，不用于统计计算。"""
    indices = np.flatnonzero(frequencies <= 1000)
    stride = max(1, math.ceil(len(indices) / MAX_SPECTRUM_POINTS))
    selected = [int(group[np.argmax(density[group])])
                for start in range(0, len(indices), stride)
                if len(group := indices[start:start + stride])]
    return frequencies[selected].tolist(), density[selected].tolist(), len(indices)


def analyze_rotating(body: RotatingBody) -> dict:
    body = body if isinstance(body, RotatingBody) else RotatingBody.model_validate(body)
    values = (import_waveform(body.csv_text, body.sample_rate_hz) if body.csv_text is not None
              else np.asarray(body.values, dtype=np.float64))
    duration = len(values) / body.sample_rate_hz
    if not 1 <= duration <= 10:
        raise ValueError('波形须为1–10秒且最多200000点')
    if not np.isfinite(values).all() or np.max(np.abs(values)) > 1e6:
        raise ValueError('幅值须为绝对值不超过1e6的有限数')
    result = {
        'method': 'bearing_envelope_evidence', 'status': 'unknown',
        'sample_rate_hz': body.sample_rate_hz, 'samples': len(values),
        'duration_s': duration, 'unit': body.unit, 'band_hz': list(body.band_hz),
        'rpm': body.rpm, 'candidates': [], 'frequency_hz': [], 'envelope_psd': [],
        'spectrum_display': {'max_points': MAX_SPECTRUM_POINTS, 'max_hz': 1000,
                             'method': 'peak_preserving_max_bins'},
        'sampling_timestamps_checked': body.csv_text is not None,
        'review_required': True, 'production_approval': False,
        'supports_rul': False, 'rul_cycles': None, 'supports_fault_root_cause': False,
        'assumptions': ['uniform_acceleration_samples', 'constant_measured_speed',
                        'orders_match_actual_bearing_geometry',
                        'sensor_mounting_and_band_suitable_for_bearing_resonance'],
        'evidence_score_meaning': 'sum_log1p_harmonic_peak_to_local_median_psd_ratio_not_probability',
        'source': 'weihu/phm_core/rotating.py', 'source_revision': SOURCE_REVISION, 'license': 'MIT',
    }
    centered = values - values.mean()
    rms = float(np.sqrt(np.mean(centered ** 2)))
    result.update(rms=rms, crest_factor=float(np.abs(centered).max() / max(rms, 1e-12)))
    if rms <= 1e-12 * max(float(np.max(np.abs(values))), 1):
        result['reason'] = 'signal_has_no_usable_variation'
        return result
    try:
        from scipy.signal import butter, hilbert, periodogram, sosfiltfilt
    except (ImportError, OSError):
        raise MechanismUnavailable('轴承包络分析需安装requirements-mechanism.txt') from None
    # 保留上游4阶零相位带通、Hilbert包络、Hann功率谱与三谐波证据。
    filtered = sosfiltfilt(butter(4, body.band_hz, btype='bandpass',
                                 fs=body.sample_rate_hz, output='sos'), centered)
    envelope = np.abs(hilbert(filtered))
    frequencies, density = periodogram(envelope - envelope.mean(),
                                       fs=body.sample_rate_hz, window='hann')
    if not np.isfinite(density).all() or not np.isfinite(filtered).all():
        result['reason'] = 'analysis_nonfinite'
        return result
    if not np.any(density > 0):
        result['reason'] = 'envelope_has_no_usable_variation'
        return result
    for name, order in body.orders.items():
        target = order * body.rpm / 60
        peaks = []
        for harmonic in (1, 2, 3):
            frequency = target * harmonic
            bins = np.abs(frequencies - frequency) <= max(2, frequency * .02)
            background = (np.abs(frequencies - frequency) < max(15, frequency * .15)) & ~bins
            if not bins.any() or not background.any():
                continue
            index = np.flatnonzero(bins)[density[bins].argmax()]
            ratio = float(density[index] / max(float(np.median(density[background])), 1e-18))
            peaks.append({'harmonic': harmonic, 'expected_hz': frequency,
                          'observed_hz': float(frequencies[index]), 'contrast': ratio})
        result['candidates'].append({'candidate': name, 'order': order, 'frequency_hz': target,
                                     'evidence_score': float(sum(np.log1p(p['contrast']) for p in peaks)),
                                     'peaks': peaks})
    result['candidates'].sort(key=lambda x: x['evidence_score'], reverse=True)
    shown_f, shown_psd, full_count = _display_spectrum(frequencies, density)
    result.update(status='evidence_only', frequency_hz=shown_f, envelope_psd=shown_psd)
    result['spectrum_display']['full_bin_count'] = full_count
    return result
