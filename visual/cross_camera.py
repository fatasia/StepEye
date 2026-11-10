"""Receipt-aligned ground-plane candidates; no ReID or confirmed inventory count."""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .calibration import _pixels, _project, _within


class Member(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    station: str = Field(min_length=1, max_length=160)
    calibration_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    image_size: tuple[int, int]


class GroupBody(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    coordinate_frame_id: str = Field(min_length=1, max_length=160)
    time_mode: Literal['receipt_aligned'] = 'receipt_aligned'
    members: list[Member] = Field(min_length=2, max_length=16)
    labels: list[str] = Field(min_length=1, max_length=100)
    max_distance_m: float = Field(default=0.5, gt=0, le=20)
    max_skew_s: float = Field(default=0.2, gt=0, le=2)
    ttl_s: float = Field(default=1, gt=0, le=10)
    min_confidence: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode='after')
    def distinct(self):
        if len({m.station for m in self.members}) != len(self.members):
            raise ValueError('组内工位不能重复')
        if any(not label.strip() for label in self.labels):
            raise ValueError('类别不能为空')
        return self


class CrossCameraStore:
    """Small atomic JSON config; matching runs on demand, never on camera threads."""

    def __init__(self, path: Path, calibrations, snapshot_getter):
        self.path = Path(path)
        self.calibrations = calibrations
        self.snapshot_getter = snapshot_getter
        self._lock = threading.RLock()
        self._groups = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
        for value in self._groups.values():
            GroupBody.model_validate(value)

    def list(self):
        with self._lock:
            return [{'id': key, **json.loads(json.dumps(value))} for key, value in self._groups.items()]

    def get(self, group_id):
        with self._lock:
            if group_id not in self._groups:
                raise KeyError('多机位组不存在')
            return GroupBody.model_validate(self._groups[group_id])

    def _calibration(self, member):
        if member.scope_id != member.station:
            raise ValueError('标定范围必须对应成员工位')
        record = self.calibrations.get(member.calibration_id)
        if (not record['validated'] or record['plane'] != 'ground' or record['unit'] != 'm'
                or record['scope_id'] != member.scope_id or record['reference'] != member.reference
                or tuple(record['image_size']) != member.image_size):
            raise ValueError('成员标定不匹配或未通过验证')
        return record

    def save(self, group_id: str, body: GroupBody, *, authorize_previous=None):
        if not group_id or len(group_id) > 160:
            raise ValueError('组 ID 无效')
        with self._lock:
            if authorize_previous is not None and group_id in self._groups:
                authorize_previous(GroupBody.model_validate(self._groups[group_id]))
            for member in body.members:
                self._calibration(member)
            updated = {**self._groups, group_id: body.model_dump(mode='json')}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(json.dumps(updated, ensure_ascii=False, allow_nan=False), 'utf-8')
            temporary.replace(self.path)
            self._groups = updated
        return {'id': group_id, **body.model_dump(mode='json')}

    def delete(self, group_id, *, authorize):
        with self._lock:
            previous = self.get(group_id)
            authorize(previous)
            updated = {key: value for key, value in self._groups.items() if key != group_id}
            temporary = self.path.with_suffix(self.path.suffix + '.tmp')
            temporary.write_text(json.dumps(updated, ensure_ascii=False, allow_nan=False), 'utf-8')
            temporary.replace(self.path)
            self._groups = updated
        return {'deleted': group_id}

    def snapshot(self, group_id, *, now=None, body=None):
        body = self.get(group_id) if body is None else body
        points, members, times = [], [], []
        for member in body.members:
            try:
                record = self._calibration(member)
                snap = self.snapshot_getter(member.station)
                if not snap or not snap['available']:
                    raise ValueError('unavailable')
                if snap['source_type'] not in {'rtsp', 'camera', 'industrial'}:
                    raise ValueError('independent_time_axis')
                if not set(body.labels).issubset(snap['available_labels']):
                    raise ValueError('labels_unavailable')
                received = float(snap['received_mono'])
                age = (time.monotonic() if now is None else now) - received
                if not math.isfinite(received) or not 0 <= age <= body.ttl_s:
                    raise ValueError('stale')
                if snap['station'] != member.station or tuple(snap['frame_size']) != member.image_size:
                    raise ValueError('frame_mismatch')
                current, ids = [], set()
                for track in snap['tracks']:
                    if track['label'] not in body.labels:
                        continue
                    confidence = float(track['confidence'])
                    if not math.isfinite(confidence):
                        raise ValueError('invalid_track')
                    if confidence < body.min_confidence:
                        continue
                    tid = track['track_id']
                    if tid in ids:
                        raise ValueError('duplicate_track')
                    ids.add(tid)
                    x0, y0, x1, y1 = track['xyxy']
                    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
                        raise ValueError('invalid_box')
                    pixel = _pixels([[(x0 + x1) / 2, y1]], member.image_size)
                    _within(pixel, record['coverage'])
                    xy = _project(pixel, np.array(record['matrix']))[0].tolist()
                    current.append({'station': member.station, 'track_id': tid,
                                    'epoch': snap['epoch'], 'generation': snap['generation'],
                                    'frame_seq': snap['frame_seq'], 'label': track['label'], 'ground_m': xy})
                points.extend(current)
                times.append(received)
                members.append({'station': member.station, 'status': 'observed', 'visible_count': len(current)})
            except (ValueError, KeyError, TypeError) as exc:
                members.append({'station': member.station, 'status': 'unknown', 'reason': str(exc)})
        result = {'id': group_id, 'coordinate_frame_id': body.coordinate_frame_id,
                  'time_mode': body.time_mode, 'status': 'unknown', 'members': members,
                  'visible_count': len(points), 'observations': points, 'candidate_groups': [],
                  'ambiguous_observations': [], 'unmatched_observations': []}
        # Partial cameras and skew never become a zero/confirmed group count.
        if any(m['status'] != 'observed' for m in members):
            result['reason'] = 'member_unknown'
            return result
        if len(points) > 512:
            result['reason'] = 'observation_limit'
            return result
        if max(times) - min(times) > body.max_skew_s:
            result['reason'] = 'receipt_skew'
            return result
        groups, ambiguous, unmatched = associate(points, body.max_distance_m)
        result.update(status='candidates', candidate_groups=groups,
                      ambiguous_observations=ambiguous, unmatched_observations=unmatched)
        return result


def associate(points, max_distance):
    """Accept only complete, one-observation-per-camera components (no chain union)."""
    edges = {i: set() for i in range(len(points))}
    for i, left in enumerate(points):
        for j in range(i + 1, len(points)):
            right = points[j]
            if (left['station'] != right['station'] and left['label'] == right['label']
                    and math.dist(left['ground_m'], right['ground_m']) <= max_distance):
                edges[i].add(j)
                edges[j].add(i)
    groups, ambiguous, unmatched, seen = [], [], [], set()
    for seed in edges:
        if seed in seen:
            continue
        component, pending = set(), [seed]
        while pending:
            node = pending.pop()
            if node not in component:
                component.add(node)
                pending.extend(edges[node] - component)
        seen.update(component)
        ordered = sorted(component)
        if len(component) == 1:
            unmatched.extend(ordered)
        elif (len({points[i]['station'] for i in component}) == len(component)
              and all(edges[i] == component - {i} for i in component)):
            groups.append(ordered)
        else:
            ambiguous.extend(ordered)
    return groups, ambiguous, unmatched
