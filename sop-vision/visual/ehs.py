"""S1 区域入侵、S5 持续烟火：仅消费当前帧轨迹，不推断 PPE 缺失。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EhsRuleCfg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64)
    kind: Literal['intrusion', 'fire_smoke']
    zone: str
    labels: list[str] = Field(default_factory=lambda: ['person'], min_length=1, max_length=32)
    min_s: float = Field(default=1., ge=0, le=3600)
    min_frames: int = Field(default=3, ge=2, le=10000)
    min_confidence: float = Field(default=.5, ge=0, le=1)
    severity: Literal['warn', 'error'] = 'warn'


class EhsCfg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    max_gap_s: float = Field(default=.5, gt=0, le=10)
    rules: list[EhsRuleCfg] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError('EHS rule id 重复')
        return self


@dataclass
class Hold:
    since: float
    frames: int
    first_area: float
    area: float


class EhsMonitor:
    def __init__(self, cfg: EhsCfg, geoms):
        self.cfg, self.geoms = cfg, geoms
        self.reset()

    def reset(self):
        self._last_ts = None
        self._holds = {}
        self._fired = set()
        self._states = {r.id: {'status': 'unknown', 'track_ids': []} for r in self.cfg.rules}

    def snapshot(self):
        return {'enabled': self.cfg.enabled,
                'rules': {k: {**v, 'track_ids': list(v['track_ids'])} for k, v in self._states.items()}}

    def process(self, tracks, ts, *, available=True, available_labels=None):
        import math
        if not available or not math.isfinite(ts):
            self.reset()
            return []
        if self._last_ts is not None:
            if ts == self._last_ts:
                return []
            if ts < self._last_ts or ts - self._last_ts > self.cfg.max_gap_s:
                self.reset()
        self._last_ts = ts
        # 丢失轨迹、过去帧框不能作为当前视觉证据。
        fresh = [t for t in tracks if t.last_seen == ts]
        alerts, active_keys = [], set()
        for rule in self.cfg.rules:
            if available_labels is not None and not set(rule.labels).issubset(available_labels):
                self._states[rule.id] = {'status': 'unknown', 'track_ids': []}
                self._fired.discard(rule.id)
                continue
            geom = self.geoms[rule.zone]
            matches = []
            for track in fresh:
                if track.label not in rule.labels or track.confidence < rule.min_confidence:
                    continue
                x0,y0,x1,y1 = track.xyxy
                # 人员用脚底中心；烟火用框中心，不要求物理标定。
                y = y1 if rule.kind == 'intrusion' else (y0+y1)/2
                if geom.contains((x0+x1)/2, y):
                    matches.append(track)
            matched_ids, durations, growth = [], [], []
            for track in matches:
                key = (rule.id, track.track_id)
                active_keys.add(key)
                area = (track.xyxy[2]-track.xyxy[0])*(track.xyxy[3]-track.xyxy[1])
                hold = self._holds.get(key)
                if hold is None:
                    hold = Hold(ts,0,area,area)
                    self._holds[key] = hold
                hold.frames += 1
                hold.area = area
                if hold.frames >= rule.min_frames and ts-hold.since >= rule.min_s:
                    matched_ids.append(track.track_id)
                    durations.append(ts-hold.since)
                    growth.append(area/hold.first_area-1 if hold.first_area else 0.)
            status = 'alert' if matched_ids else ('pending' if matches else 'clear')
            self._states[rule.id] = {'status': status, 'track_ids': [t.track_id for t in matches], 'observed_at': ts}
            if not matches:
                self._fired.discard(rule.id)
            if matched_ids and rule.id not in self._fired:
                self._fired.add(rule.id)
                alerts.append({'rule': rule.id, 'name': rule.id, 'kind': rule.kind,
                               'zone': rule.zone, 'severity': rule.severity, 'ts': ts,
                               'duration_s': round(max(durations),3), 'track_ids': matched_ids,
                               'area_change_ratio': max(growth) if rule.kind == 'fire_smoke' else None,
                               'message': '区域入侵持续观测' if rule.kind == 'intrusion' else '烟火持续观测',
                               'source': 'ehs_tracks'})
        self._holds = {k:v for k,v in self._holds.items() if k in active_keys}
        return alerts
