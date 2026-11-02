"""常驻安全规则执行器:与生产周期无关的持续监控规则(S16 无人值守 / S17 人员聚集 / S4 跌倒 / S15 手机等)。

与 SOP 引擎的分工:引擎管"工件周期内的步骤合规";本模块管"任何时刻都该成立的岗位状态"。
纯逻辑、事件时间戳驱动、可离线测试;运行时只需把感知事件喂给 process() 并周期调用 tick()。
每条规则一个微型状态机:保持 → 计时 → 触发一次 → 状态解除后重新武装(避免告警风暴)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .events import (HAND_ENTER, HAND_EXIT, OBJECT_COUNT, OBJECT_ENTER, OBJECT_EXIT, PERSON_COUNT,
                     PERSON_ENTER, PERSON_EXIT, PERSON_FALL, Event)

RuleKind = Literal["person_absent", "person_count", "fall", "object_present"]


@dataclass(slots=True)
class SafetyRule:
    id: str
    kind: RuleKind
    zone: str
    min_s: float = 5.0  # 条件需持续时长(防瞬时误报)
    count_ge: int = 1  # person_count / object_present 专用:数量阈值
    severity: str = "warn"
    name: str = ""
    target: str = ""  # object_present 专用:要盯的类别名

    def describe(self) -> str:
        if self.kind == "person_absent":
            return f"「{self.zone}」无人超过 {self.min_s:g}s"
        if self.kind == "fall":
            return f"「{self.zone}」出现倒地姿态持续 {self.min_s:g}s"
        if self.kind == "object_present":
            return f"「{self.zone}」检测到「{self.target}」≥{self.count_ge} 持续 {self.min_s:g}s"
        return f"「{self.zone}」人数 ≥{self.count_ge} 持续 {self.min_s:g}s"


@dataclass
class SafetyMonitor:
    rules: list[SafetyRule]
    _absent_since: dict[str, float] = field(default_factory=dict)
    _count: dict[str, int] = field(default_factory=dict)
    _fall_since: dict[str, float] = field(default_factory=dict)  # zone -> 倒地开始时刻
    _obj: dict[tuple[str, str], int] = field(default_factory=dict)  # (zone, 类别) -> 数量
    _hold_since: dict[str, float] = field(default_factory=dict)  # rule_id -> 条件开始持续的时刻
    _fired: set[str] = field(default_factory=set)
    now: float = 0.0

    def process(self, ev: Event) -> list[dict[str, Any]]:
        """喂感知事件;返回本次新触发的告警。"""
        self.now = max(ev.ts, self.now)
        if ev.zone is None:
            return []
        z = ev.zone
        if ev.type == PERSON_ENTER:
            self._absent_since.pop(z, None)
            self._count[z] = max(1, self._count.get(z, 1))
        elif ev.type == PERSON_EXIT:
            self._absent_since.setdefault(z, ev.ts)
            self._count[z] = 0
        elif ev.type == PERSON_COUNT:
            n = int(ev.value or 0)
            self._count[z] = n
            if n > 0:
                self._absent_since.pop(z, None)
            else:
                self._absent_since.setdefault(z, ev.ts)
        elif ev.type == PERSON_FALL:
            if int(ev.value or 0) > 0:
                self._fall_since.setdefault(z, ev.ts)
            else:
                self._fall_since.pop(z, None)  # 姿态恢复:重新武装
        elif ev.type == OBJECT_COUNT:
            self._obj[(z, str(ev.name))] = int(ev.value or 0)
        elif ev.type == OBJECT_ENTER:
            self._obj[(z, str(ev.name))] = max(1, self._obj.get((z, str(ev.name)), 1))
        elif ev.type == OBJECT_EXIT:
            self._obj[(z, str(ev.name))] = 0
        elif ev.type == HAND_ENTER:
            self._absent_since.pop(f"hand:{z}", None)
        elif ev.type == HAND_EXIT:
            self._absent_since.setdefault(f"hand:{z}", ev.ts)
        return self.tick(self.now)

    def tick(self, ts: float) -> list[dict[str, Any]]:
        """周期调用(无事件也要走时钟);状态解除的规则重新武装。"""
        self.now = max(ts, self.now)
        alerts: list[dict[str, Any]] = []
        for r in self.rules:
            ok = self._satisfied(r, ts)
            if not ok:
                self._hold_since.pop(r.id, None)
                self._fired.discard(r.id)  # 解除后重新武装
                continue
            since = self._hold_since.setdefault(r.id, ts)
            if r.id not in self._fired and ts - since >= r.min_s:
                self._fired.add(r.id)
                alerts.append({"rule": r.id, "name": r.name or r.id, "kind": r.kind, "zone": r.zone,
                               "severity": r.severity, "ts": round(ts, 3), "duration_s": round(ts - since, 2),
                               "message": f"安全规则触发:{r.describe()}"})
        return alerts

    def _satisfied(self, r: SafetyRule, ts: float) -> bool:
        if r.kind == "person_absent":
            return r.zone in self._absent_since
        if r.kind == "person_count":
            return self._count.get(r.zone, 0) >= r.count_ge
        if r.kind == "fall":
            return r.zone in self._fall_since
        if r.kind == "object_present":
            return self._obj.get((r.zone, r.target), 0) >= max(1, r.count_ge)
        return False
