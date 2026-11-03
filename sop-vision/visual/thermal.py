"""S6 thermal rules over admitted device signals, independent of visual frame quality."""
from __future__ import annotations

import copy
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .events import SIGNAL
from .fusion import IDENTIFIER


class ThermalRule(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=IDENTIFIER)
    source: str = Field(pattern=IDENTIFIER)
    name: str = Field(pattern=IDENTIFIER)
    kind: Literal['temperature', 'rise_rate', 'hotspot_area']
    threshold: float
    min_s: float = Field(default=1., ge=0, le=3600)
    min_samples: int = Field(default=3, ge=2, le=10000)
    max_gap_s: float = Field(default=2., gt=0, le=600)
    min_dt_s: float = Field(default=.01, gt=0, le=10)
    max_abs_rate_c_s: float = Field(default=200., gt=0, le=10000)
    area_unit: Literal['ratio', 'm2'] = 'ratio'
    hotspot_above_c: float | None = Field(default=None, ge=-273.15, le=4000)
    severity: Literal['warn', 'error'] = 'warn'

    @model_validator(mode='after')
    def validate_units(self):
        if self.source == 'legacy-http':
            raise ValueError('温场规则需要明确设备来源，不能使用兼容人工信号')
        if self.min_dt_s >= self.max_gap_s:
            raise ValueError('温场采样最小间隔须小于最大间隔')
        if self.kind == 'temperature' and not -273.15 <= self.threshold <= 4000:
            raise ValueError('温度阈值超出支持范围')
        if self.kind != 'temperature' and self.threshold <= 0:
            raise ValueError('温升或面积阈值须为正数')
        if self.kind == 'rise_rate' and self.threshold >= self.max_abs_rate_c_s:
            raise ValueError('温升阈值须小于异常变化过滤上限')
        if self.kind == 'hotspot_area':
            if self.hotspot_above_c is None or (self.area_unit == 'ratio' and self.threshold > 1):
                raise ValueError('热点面积需定义温度分界与正确面积单位')
        return self


class ThermalCfg(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    rules: list[ThermalRule] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError('温场规则 id 重复')
        if self.enabled and not self.rules:
            raise ValueError('启用温场监测需要规则')
        return self


class ThermalSample(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    temperature: float = Field(ge=-273.15, le=4000, strict=True)
    temperature_unit: Literal['C']
    hotspot_area: float | None = Field(default=None, ge=0, le=1000000)
    area_unit: Literal['ratio', 'm2'] | None = None
    hotspot_above_c: float | None = Field(default=None, ge=-273.15, le=4000)

    @model_validator(mode='after')
    def area_range(self):
        if self.hotspot_area is not None:
            if self.area_unit is None or self.hotspot_above_c is None:
                raise ValueError('热点面积缺少单位或温度分界')
            if self.area_unit == 'ratio' and self.hotspot_area > 1:
                raise ValueError('热点比例必须在0至1之间')
        return self


class ThermalMonitor:
    def __init__(self, cfg: ThermalCfg):
        self.cfg = cfg
        self._heads = {}
        self.reset()

    def reset(self, reason='awaiting_signal'):
        self._last_ts = None
        self._previous = {}
        self._holds = {}
        self._fired = set()
        self._expires = {}
        self._last_frame = {}
        self._states = {r.id: {'status':'unknown', 'reason':reason} for r in self.cfg.rules}

    def snapshot(self):
        return {'enabled':self.cfg.enabled, 'rules':copy.deepcopy(self._states)}

    def _unknown(self, rule, reason):
        self._holds.pop(rule.id, None)
        self._fired.discard(rule.id)
        self._states[rule.id] = {'status':'unknown', 'reason':reason}

    def process(self, events, ts):
        if not self.cfg.enabled:
            return []
        if not math.isfinite(ts) or (self._last_ts is not None and ts < self._last_ts):
            self.reset('invalid_frame_time')
            return []
        self._last_ts = ts
        for rule in self.cfg.rules:
            if ts >= self._expires.get(rule.id, float('-inf')) or ts - self._last_frame.get(rule.id, float('-inf')) > rule.max_gap_s:
                self._unknown(rule, 'signal_expired')
                self._previous.pop((rule.source, rule.name), None)
        alerts = []
        for event in events:
            rules = [r for r in self.cfg.rules if event.type == SIGNAL and event.source == r.source and event.name == r.name]
            if not rules:
                continue
            key = (event.source, event.name)
            seq = event.data.get('sequence')
            observed = event.data.get('observed_at')
            expiry = event.data.get('valid_until_ts')
            valid_numbers = (type(seq) is int and seq >= 0 and isinstance(observed,(float,int))
                             and not isinstance(observed,bool) and math.isfinite(observed) and observed > 0
                             and isinstance(expiry,(float,int)) and not isinstance(expiry,bool) and math.isfinite(expiry))
            if (not valid_numbers or seq <= self._heads.get(key, -1) or not math.isfinite(event.conf) or event.conf <= 0
                    or event.data.get('quality') != 'ok' or expiry <= ts or event.ts != ts):
                self._previous.pop(key, None)
                for rule in rules:
                    self._unknown(rule, 'invalid_signal_provenance')
                continue
            self._heads[key] = seq
            try:
                sample = ThermalSample.model_validate(event.value)
            except ValueError:
                self._previous.pop(key, None)
                for rule in rules:
                    self._unknown(rule, 'invalid_units_or_value')
                continue
            previous = self._previous.get(key)
            dt = observed - previous[0] if previous else None
            rate = (sample.temperature - previous[1]) / dt if dt is not None and dt > 0 else None
            self._previous[key] = (observed, sample.temperature)
            for rule in rules:
                self._expires[rule.id] = expiry
                self._last_frame[rule.id] = ts
                if previous and (dt < rule.min_dt_s or dt > rule.max_gap_s
                                 or rate is None or abs(rate) > rule.max_abs_rate_c_s):
                    self._unknown(rule, 'invalid_sample_interval_or_jump')
                    continue
                if rule.kind == 'rise_rate':
                    metric, unit = rate, 'C/s'
                elif rule.kind == 'temperature':
                    metric, unit = sample.temperature, 'C'
                else:
                    metric, unit = sample.hotspot_area, rule.area_unit
                    if sample.area_unit != rule.area_unit or sample.hotspot_above_c != rule.hotspot_above_c:
                        metric = None
                if metric is None:
                    self._unknown(rule, 'missing_metric_or_definition')
                    continue
                active = metric >= rule.threshold
                state = {'status':'clear', 'reason':'', 'value':metric, 'unit':unit,
                         'sequence':seq, 'observed_at':observed, 'valid_until_ts':expiry}
                if not active:
                    self._holds.pop(rule.id, None)
                    self._fired.discard(rule.id)
                else:
                    since, count = self._holds.get(rule.id,(observed,0))
                    count += 1
                    self._holds[rule.id] = (since,count)
                    confirmed = count >= rule.min_samples and observed-since+1e-9 >= rule.min_s
                    state['status'] = 'alert' if confirmed else 'pending'
                    if confirmed and rule.id not in self._fired:
                        self._fired.add(rule.id)
                        alerts.append({'rule':rule.id, 'name':rule.id, 'kind':'thermal_'+rule.kind,
                            'message':f'{rule.id}：{metric:.2f} {unit}，阈值 {rule.threshold:g} {unit}',
                            'zone':'', 'severity':rule.severity, 'ts':ts, 'duration_s':observed-since,
                            'value':metric, 'unit':unit, 'threshold':rule.threshold,
                            'source':'thermal_signal', 'device_source':event.source, 'signal_name':event.name,
                            'sequence':seq, 'observed_at':observed,
                            'hotspot_above_c':rule.hotspot_above_c if rule.kind=='hotspot_area' else None})
                self._states[rule.id] = state
        return alerts
