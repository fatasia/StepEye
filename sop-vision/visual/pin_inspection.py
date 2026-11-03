"""Single-row connector silhouette checks for fixed, aligned fixtures (pixel units)."""
from __future__ import annotations

import hashlib
from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class PinSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    roi: tuple[int, int, int, int]
    axis: Literal['x', 'y'] = 'x'
    polarity: Literal['bright', 'dark'] = 'bright'
    expected_count: int = Field(ge=1, le=128, strict=True)
    position_tolerance_px: float = Field(default=3, ge=0, le=100)
    spacing_tolerance_px: float = Field(default=3, ge=0, le=100)


class ReviewNeeded(Exception):
    pass


def _centers(image, spec):
    x0, y0, x1, y1 = spec.roi
    gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    if spec.axis == 'y':
        gray = gray.T
    # Fixed ROI must span pin bodies, not tips: narrow specks never become a pin.
    if float(np.percentile(gray, 95) - np.percentile(gray, 5)) < 40:
        raise ReviewNeeded('low_contrast')
    threshold, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if spec.polarity == 'dark':
        mask = 255 - mask
    projection = (mask > 0).mean(axis=0)
    support = projection >= .7
    transitions = np.diff(np.pad(support.astype(np.int8), (1, 1)))
    starts, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
    if not len(starts):
        raise ReviewNeeded('no_complete_pins')
    if len(starts) > 128:
        raise ReviewNeeded('too_many_components')
    centers, widths = [], []
    for start, end in zip(starts, ends):
        if start == 0 or end == gray.shape[1]:
            raise ReviewNeeded('pin_touches_roi_border')
        if end - start < 3:
            raise ReviewNeeded('pin_too_thin')
        centers.append(float((start + end - 1) / 2 + (x0 if spec.axis == 'x' else y0)))
        widths.append(int(end - start))
    # Partial components outside complete bodies indicate occlusion, tips or mixed rows.
    if np.count_nonzero((projection > .15) & (projection < .7)) > max(2, len(centers)):
        raise ReviewNeeded('partial_or_tilted_pins')
    return {'centers': centers, 'widths': widths,
            'spacing_px': np.diff(centers).tolist(), 'threshold': float(threshold)}


def inspect_pins(reference, image, spec):
    spec = PinSpec.model_validate(spec)
    for array in (reference, image):
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
            raise ValueError('需要 uint8 RGB 图片')
        if array.shape[0] * array.shape[1] > 4_000_000 or max(array.shape[:2]) > 4096:
            raise ValueError('图片最多 400 万像素、边长 4096')
    if reference.shape != image.shape:
        raise ValueError('参考图与待检图尺寸必须一致')
    h, w = image.shape[:2]
    x0, y0, x1, y1 = spec.roi
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h and min(x1-x0, y1-y0) >= 12):
        raise ValueError('ROI 越界或小于 12 × 12 像素')
    result = {'status': 'review', 'reason': None, 'review_required': True,
              'method': 'single_row_silhouette_projection', 'unit': 'px',
              'fingerprint_basis': 'decoded_rgb_uint8',
              'reference_sha256': hashlib.sha256(reference.tobytes()).hexdigest(),
              'image_sha256': hashlib.sha256(image.tobytes()).hexdigest(),
              'image_size': [w, h], 'spec': spec.model_dump(),
              'reference': None, 'observed': None, 'missing': [], 'extra': [],
              'offsets': [], 'spacing_checks': []}
    try:
        baseline = result['reference'] = _centers(reference, spec)
        if len(baseline['centers']) != spec.expected_count:
            raise ReviewNeeded('reference_count_mismatch')
        if baseline['spacing_px'] and min(baseline['spacing_px']) <= 2 * spec.position_tolerance_px:
            raise ReviewNeeded('tolerance_overlaps_pin_positions')
        observed = result['observed'] = _centers(image, spec)
        remaining = set(range(len(observed['centers'])))
        pairs = {}
        # Half-pitch bounds stop an absent pin being silently paired to its neighbor.
        for index, center in enumerate(baseline['centers']):
            pitch = min(baseline['spacing_px']) if baseline['spacing_px'] else 2 * max(3, spec.position_tolerance_px + 1)
            candidates = [j for j in remaining if abs(observed['centers'][j]-center) < pitch / 2]
            if not candidates:
                result['missing'].append({'index': index+1, 'center': center})
                continue
            j = min(candidates, key=lambda k: abs(observed['centers'][k]-center))
            remaining.remove(j)
            pairs[index] = observed['centers'][j]
            delta = pairs[index]-center
            result['offsets'].append({'index':index+1, 'reference':center, 'observed':pairs[index],
                                      'delta_px':delta, 'within_tolerance':abs(delta) <= spec.position_tolerance_px})
        result['extra'] = [observed['centers'][j] for j in sorted(remaining)]
        for index, expected in enumerate(baseline['spacing_px']):
            if index not in pairs or index+1 not in pairs:
                continue
            measured = pairs[index+1] - pairs[index]
            result['spacing_checks'].append({'from_index':index+1, 'to_index':index+2,
                'reference_px':expected, 'measured_px':measured, 'delta_px':measured-expected,
                'within_tolerance':abs(measured-expected) <= spec.spacing_tolerance_px})
        deviation = (result['missing'] or result['extra'] or
                     any(not r['within_tolerance'] for r in result['offsets'] + result['spacing_checks']))
        result['status'] = 'deviation' if deviation else 'matched'
    except ReviewNeeded as exc:
        result['reason'] = str(exc)
    return result
