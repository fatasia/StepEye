"""Ground-plane proximity and speed from current same-ID object tracks."""
from collections import deque
import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .calibration import _pixels, _within, _project


class ProximityCfg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    calibration_id: str = Field(default='', max_length=100)
    scope_id: str = Field(default='', max_length=160)
    reference: str = Field(default='', max_length=200)
    image_size: tuple[int, int] = (640, 480)
    person_labels: list[str] = Field(default_factory=lambda: ['person'], min_length=1, max_length=32)
    vehicle_labels: list[str] = Field(default_factory=lambda: ['forklift'], min_length=1, max_length=32)
    near_m: float = Field(default=2., gt=0, le=100)
    speed_mps: float = Field(default=3., gt=0, le=100)
    min_confidence: float = Field(default=.5, ge=0, le=1)
    min_frames: int = Field(default=3, ge=2, le=1000)
    min_s: float = Field(default=.5, ge=0, le=60)
    min_dt: float = Field(default=.01, gt=0, le=1)
    max_gap_s: float = Field(default=.5, gt=0, le=10)
    speed_window_s: float = Field(default=.3, gt=0, le=5)
    max_speed_mps: float = Field(default=30., gt=0, le=200)
    severity: Literal['warn', 'error'] = 'warn'

    @model_validator(mode='after')
    def validate_binding(self):
        if self.enabled and not all(x.strip() for x in (self.calibration_id, self.scope_id, self.reference)):
            raise ValueError('启用人车距离/速度必须绑定标定版本与画面状态')
        if self.min_dt >= self.max_gap_s or self.speed_mps >= self.max_speed_mps:
            raise ValueError('时间间隔或速度过滤上限配置错误')
        if set(self.person_labels) & set(self.vehicle_labels):
            raise ValueError('人员与车辆类别不能重叠')
        if any(not 2 <= x <= 100000 for x in self.image_size):
            raise ValueError('标定画面尺寸无效')
        return self


class ProximityMonitor:
    def __init__(self, cfg: ProximityCfg, frame_size):
        self.cfg, self.frame_size = cfg, tuple(frame_size)
        self.record = None
        self.binding_error = 'calibration_unbound'
        self.reset()

    def reset(self, reason='observations_reset'):
        self._last_ts = None
        self._tracks = {}
        self._holds = {}
        self._fired = set()
        self._state = {'enabled': self.cfg.enabled, 'status': 'unknown', 'reason': reason,
                       'pairs': [], 'vehicles': []}

    def bind_calibrations(self, store):
        self.record = None
        self.reset('calibration_unbound')
        try:
            record = store.get(self.cfg.calibration_id)
            if (not record['validated'] or record['plane'] != 'ground' or record['unit'] != 'm'
                    or record['scope_id'] != self.cfg.scope_id or record['reference'] != self.cfg.reference
                    or tuple(record['image_size']) != self.cfg.image_size or self.frame_size != self.cfg.image_size):
                raise ValueError('calibration_binding_mismatch')
            self.record = record
            self.binding_error = ''
        except (KeyError, ValueError) as exc:
            self.binding_error = str(exc)
        self.reset(self.binding_error or 'awaiting_tracks')

    def snapshot(self):
        import copy
        return copy.deepcopy(self._state)

    def _confirm(self, key, ts, active, evidence, kind):
        if not active:
            self._holds.pop(key, None)
            self._fired.discard(key)
            return None
        since, frames = self._holds.get(key, (ts, 0))
        self._holds[key] = (since, frames + 1)
        if frames + 1 < self.cfg.min_frames or ts - since + 1e-9 < self.cfg.min_s:
            return None
        if key in self._fired:
            return None
        self._fired.add(key)
        return {'rule': kind, 'name': '人车接近' if kind == 'vehicle_proximity' else '车辆超速',
                'message': '人车距离低于设定值' if kind == 'vehicle_proximity' else '车辆速度超过设定值',
                'kind': kind, 'zone': '', 'severity': self.cfg.severity, 'ts': ts,
                'duration_s': ts - since, 'source': 'ground_tracks',
                'calibration_id': self.record['id'], 'calibration_version': self.record['version'],
                'validation_max_m': self.record['validation_max'], **evidence}

    def process(self, tracks, ts, *, available=True, available_labels=None, frame_size=None):
        if not self.cfg.enabled:
            return []
        if (self.record is None or not available or not math.isfinite(ts)
                or (frame_size is not None and tuple(frame_size) != self.frame_size)):
            self.reset(self.binding_error if self.record is None else 'perception_unavailable')
            return []
        required = set(self.cfg.person_labels + self.cfg.vehicle_labels)
        if available_labels is not None and not required.issubset(available_labels):
            self.reset('required_labels_unavailable')
            return []
        if self._last_ts is not None:
            dt = ts - self._last_ts
            if dt < self.cfg.min_dt or dt > self.cfg.max_gap_s:
                self.reset('invalid_time_gap')
                return []
        self._last_ts = ts
        relevant = [t for t in tracks if t.label in required]
        if any(t.last_seen != ts or not math.isfinite(t.confidence)
               or t.confidence < self.cfg.min_confidence for t in relevant):
            self.reset('stale_or_uncertain_tracks')
            return []
        fresh = relevant
        if len(fresh) > 256 or len({t.track_id for t in fresh}) != len(fresh):
            self.reset('invalid_tracks')
            return []
        positions, labels = {}, {}
        try:
            for track in fresh:
                x0, y0, x1, y1 = track.xyxy
                if not all(math.isfinite(v) for v in track.xyxy) or x1 <= x0 or y1 <= y0:
                    raise ValueError('invalid_box')
                pixel = _pixels([[(x0 + x1) / 2, y1]], self.frame_size)
                _within(pixel, self.record['coverage'])
                positions[track.track_id] = _project(pixel, np.array(self.record['matrix']))[0]
                labels[track.track_id] = track.label
        except ValueError:
            self.reset('outside_calibrated_plane')
            return []
        velocities = {}
        for tid, position in positions.items():
            history = self._tracks.get(tid)
            if history and history[-1][2] != labels[tid]:
                self.reset('track_identity_changed')
                return []
            if history:
                dt = ts - history[-1][0]
                if np.linalg.norm(position - history[-1][1]) / dt > self.cfg.max_speed_mps:
                    self.reset('motion_outlier')
                    return []
            else:
                history = deque(maxlen=256)
                self._tracks[tid] = history
            history.append((ts, position, labels[tid]))
            # Keep the oldest sample spanning the requested smoothing window.
            while len(history) > 2 and ts - history[1][0] >= self.cfg.speed_window_s:
                history.popleft()
            if len(history) >= 2 and ts - history[0][0] >= self.cfg.speed_window_s - 1e-9:
                velocities[tid] = (position - history[0][1]) / (ts - history[0][0])
        self._tracks = {k: v for k, v in self._tracks.items() if k in positions}
        people = [tid for tid in positions if labels[tid] in self.cfg.person_labels]
        vehicles = [tid for tid in positions if labels[tid] in self.cfg.vehicle_labels]
        pairs, speeds, alerts, keys = [], [], [], set()
        for vehicle in vehicles:
            speed = float(np.linalg.norm(velocities[vehicle])) if vehicle in velocities else None
            evidence = {'track_ids': [vehicle], 'speed_mps': speed}
            speeds.append(evidence)
            key = ('speed', vehicle)
            keys.add(key)
            alert = self._confirm(key, ts, speed is not None and speed >= self.cfg.speed_mps,
                                  evidence, 'vehicle_speed')
            if alert:
                alerts.append(alert)
            for person in people:
                distance = float(np.linalg.norm(positions[person] - positions[vehicle]))
                relative = (float(np.linalg.norm(velocities[person] - velocities[vehicle]))
                            if person in velocities and vehicle in velocities else None)
                evidence = {'track_ids': [person, vehicle], 'distance_m': distance,
                            'relative_speed_mps': relative, 'vehicle_speed_mps': speed}
                pairs.append(evidence)
                key = ('near', person, vehicle)
                keys.add(key)
                alert = self._confirm(key, ts, distance <= self.cfg.near_m, evidence, 'vehicle_proximity')
                if alert:
                    alerts.append(alert)
        self._holds = {k: v for k, v in self._holds.items() if k in keys}
        self._fired.intersection_update(keys)
        status = 'alert' if self._fired else ('pending' if self._holds else 'clear')
        self._state = {'enabled': True, 'status': status, 'reason': '', 'observed_at': ts,
                       'pairs': pairs, 'vehicles': speeds, 'calibration_id': self.record['id'],
                       'speed_status': 'unknown' if any(v['speed_mps'] is None for v in speeds) else 'observed'}
        return alerts
