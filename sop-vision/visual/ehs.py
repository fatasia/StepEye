"""区域入侵、烟火、可见物数量与通道占用；只消费当前帧轨迹。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EhsRuleCfg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=64)
    kind: Literal['intrusion', 'fire_smoke', 'object_count', 'aisle_occupied', 'sleep_on_post']
    zone: str
    labels: list[str] = Field(default_factory=lambda: ['person'], min_length=1, max_length=32)
    min_s: float = Field(default=1., ge=0, le=3600)
    min_frames: int = Field(default=3, ge=2, le=10000)
    min_confidence: float = Field(default=.5, ge=0, le=1)
    severity: Literal['warn', 'error'] = 'warn'
    min_count: int | None = Field(default=None, ge=0, le=256, strict=True)
    max_count: int | None = Field(default=None, ge=0, le=256, strict=True)
    # 睡岗专用:中心点位移阈值(像素,与轨迹框同一像素坐标系,按机位分辨率整定)。
    max_shift_px: float = Field(default=8., gt=0, le=10000)

    @model_validator(mode='after')
    def count_limits(self):
        if self.kind == 'object_count':
            if self.min_count is None and self.max_count is None:
                raise ValueError('object_count 需要 min_count 或 max_count')
            if self.min_count is not None and self.max_count is not None and self.min_count > self.max_count:
                raise ValueError('min_count 不能大于 max_count')
        elif self.min_count is not None or self.max_count is not None:
            raise ValueError('数量上下限仅用于 object_count')
        return self


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


@dataclass
class Still:
    x: float
    y: float
    since: float
    frames: int


class EhsMonitor:
    def __init__(self, cfg: EhsCfg, geoms):
        self.cfg, self.geoms = cfg, geoms
        self.reset()

    def reset(self):
        self._last_ts = None
        self._holds = {}
        self._sleeps = {}
        self._fired = set()
        self._counts = {}
        self._states = {r.id: {'status': 'unknown', 'track_ids': []} for r in self.cfg.rules}

    def snapshot(self):
        return {'enabled': self.cfg.enabled,
                'rules': {k: {**v, 'track_ids': list(v['track_ids'])} for k, v in self._states.items()}}

    def process(self, tracks, ts, *, available=True, available_labels=None):
        import math
        if not self.cfg.enabled or not available or not math.isfinite(ts):
            self.reset()
            return []
        if self._last_ts is not None:
            if ts == self._last_ts:
                return []
            if ts < self._last_ts or ts - self._last_ts > self.cfg.max_gap_s:
                self.reset()
        self._last_ts = ts
        # 丢失轨迹、过去帧框不能作为当前视觉证据。
        fresh = list({t.track_id:t for t in tracks if t.last_seen == ts}.values())
        if len(fresh) > 256:
            self.reset()
            return []
        alerts, active_keys = [], set()
        for rule in self.cfg.rules:
            counting = rule.kind == 'object_count'
            if (counting and available_labels is None) or (available_labels is not None and not set(rule.labels).issubset(available_labels)):
                self._states[rule.id] = {'status': 'unknown', 'track_ids': []}
                self._fired.discard(rule.id)
                self._counts.pop(rule.id, None)
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
            if rule.kind == 'sleep_on_post':
                alert = self._sleep_state(rule, matches, ts)
                if alert is not None:alerts.append(alert)
                continue
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
            if counting:
                alert = self._count_state(rule, matches, matched_ids, ts)
                if alert is not None:alerts.append(alert)
                continue
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
                               'message': {'intrusion':'区域入侵持续观测', 'fire_smoke':'烟火持续观测',
                                           'aisle_occupied':'通道指定物品持续占用',
                                           'sleep_on_post':'人员持续静止超过时长(疑似睡岗)'}[rule.kind],
                               'source': 'ehs_tracks'})
        self._holds = {k:v for k,v in self._holds.items() if k in active_keys}
        return alerts

    def _count_state(self, rule, matches, stable_ids, ts):
        # 单机位当前可见计数；不累计经过量，不承诺遮挡去重或跨机位库存。
        state = self._counts.setdefault(rule.id, {'since':ts, 'frames':0, 'breach':None})
        state['frames'] += 1
        ready = (state['frames'] >= rule.min_frames and ts-state['since'] >= rule.min_s
                 and len(matches) == len(stable_ids))
        count = len(stable_ids)
        side = ('below' if rule.min_count is not None and count < rule.min_count else
                'above' if rule.max_count is not None and count > rule.max_count else None)
        snap = {'status':'pending', 'track_ids':list(stable_ids), 'observed_at':ts,
                'count':count if ready else None, 'observed_count':len(matches),
                'scope':'single_camera_visible_snapshot', 'anchor':'box_center',
                'min_count':rule.min_count, 'max_count':rule.max_count}
        self._states[rule.id] = snap
        if not ready:
            state['breach'] = None
            return None
        if side is None:
            snap['status'] = 'clear'
            state['breach'] = None
            self._fired.discard(rule.id)
            return None
        breach = state['breach']
        if breach is None or breach['side'] != side:
            if breach is not None:self._fired.discard(rule.id)
            breach = {'side':side, 'since':ts, 'frames':0}
            state['breach'] = breach
        breach['frames'] += 1
        snap['reason'] = side
        if breach['frames'] < rule.min_frames or ts-breach['since'] < rule.min_s:
            return None
        snap['status'] = 'alert'
        if rule.id in self._fired:return None
        self._fired.add(rule.id)
        return {'rule':rule.id, 'name':rule.id, 'kind':rule.kind, 'zone':rule.zone,
                'severity':rule.severity, 'ts':ts, 'duration_s':round(ts-breach['since'],3),
                'track_ids':list(stable_ids), 'count':count, 'min_count':rule.min_count,
                'max_count':rule.max_count, 'reason':side, 'scope':snap['scope'],
                'message':'区域可见物数量低于下限' if side=='below' else '区域可见物数量超过上限',
                'source':'ehs_tracks'}

    def _sleep_state(self, rule, matches, ts):
        # 睡岗:同一人员 ID 的框中心相对静止锚点的位移持续低于阈值;大幅移动或离开区域重置。
        import math
        state = self._sleeps.setdefault(rule.id, {})
        live_ids = {t.track_id for t in matches}
        for gone in set(state) - live_ids:
            del state[gone]  # 离开区域即重置,返回后重新累积
        confirmed, durations = [], []
        for track in matches:
            x, y = (track.xyxy[0]+track.xyxy[2])/2, (track.xyxy[1]+track.xyxy[3])/2
            still = state.get(track.track_id)
            if still is None:
                state[track.track_id] = Still(x, y, ts, 1)
                continue
            still.frames += 1
            if math.hypot(x-still.x, y-still.y) > rule.max_shift_px:
                # 大幅移动:锚点与静止计时重置;帧数保留,时间条件兜底重新计满。
                still.x, still.y, still.since = x, y, ts
            elif still.frames >= rule.min_frames and ts-still.since >= rule.min_s:
                confirmed.append(track.track_id)
                durations.append(ts-still.since)
        self._states[rule.id] = {'status': 'alert' if confirmed else ('pending' if matches else 'clear'),
                                 'track_ids': [t.track_id for t in matches], 'observed_at': ts}
        if not matches:
            self._fired.discard(rule.id)
        if confirmed and rule.id not in self._fired:
            self._fired.add(rule.id)
            return {'rule': rule.id, 'name': rule.id, 'kind': rule.kind,
                    'zone': rule.zone, 'severity': rule.severity, 'ts': ts,
                    'duration_s': round(max(durations), 3), 'track_ids': confirmed,
                    'area_change_ratio': None,
                    'message': '人员持续静止超过时长(疑似睡岗)', 'source': 'ehs_tracks'}
        return None
