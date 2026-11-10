"""常驻安全规则执行器离线测试:纯逻辑,事件时间戳驱动。"""
from __future__ import annotations

from visual.events import Event, PERSON_COUNT, PERSON_ENTER, PERSON_EXIT
from visual.safety import SafetyMonitor, SafetyRule

RULES = [
    SafetyRule(id="unattended", kind="person_absent", zone="post", min_s=5.0, severity="warn"),
    SafetyRule(id="crowd", kind="person_count", zone="aisle", min_s=3.0, count_ge=2, severity="error"),
]


def test_unattended_fires_after_min_s_and_rearms():
    m = SafetyMonitor(RULES)
    assert m.process(Event(1.0, PERSON_COUNT, zone="post", value=0)) == []  # 确认无人,计时开始
    assert m.tick(4.0) == []  # 3s < 5s
    alerts = m.tick(6.5)  # 无人 5.5s → 触发一次
    assert len(alerts) == 1 and alerts[0]["rule"] == "unattended" and alerts[0]["duration_s"] == 5.5
    assert m.tick(8.0) == []  # 已触发,不重复告警
    m.process(Event(9.0, PERSON_ENTER, zone="post"))  # 人来了 → 解除并重新武装
    assert m.tick(10.0) == []
    m.process(Event(11.0, PERSON_EXIT, zone="post"))  # 又走了 → 重新计时
    assert m.tick(13.0) == []
    assert len(m.tick(16.5)) == 1  # 再次无人 5.5s → 再次触发


def test_unattended_needs_confirmed_zero_before_counting():
    m = SafetyMonitor(RULES)
    assert m.tick(30.0) == []  # 从未确认过人数:不能凭空报"无人值守"


def test_crowd_fires_on_threshold_and_clears():
    m = SafetyMonitor(RULES)
    m.process(Event(1.0, PERSON_COUNT, zone="aisle", value=2))
    assert m.tick(2.0) == []
    alerts = m.tick(4.5)  # ≥2 人持续 3.5s
    assert len(alerts) == 1 and alerts[0]["rule"] == "crowd" and alerts[0]["severity"] == "error"
    m.process(Event(5.0, PERSON_COUNT, zone="aisle", value=1))  # 人数降下来 → 解除
    assert m.tick(6.0) == []
    m.process(Event(7.0, PERSON_COUNT, zone="aisle", value=2))
    assert len(m.tick(10.5)) == 1  # 重新武装后再触发


def test_absent_cleared_by_count_event_too():
    m = SafetyMonitor([RULES[0]])
    m.process(Event(1.0, PERSON_COUNT, zone="post", value=0))
    m.process(Event(2.0, PERSON_COUNT, zone="post", value=1))  # 计数事件同样能解除无人
    assert m.tick(20.0) == []


def test_rules_are_independent_per_zone():
    m = SafetyMonitor(RULES)
    m.process(Event(1.0, PERSON_COUNT, zone="other", value=0))  # 其他区域无人与本规则无关
    assert m.tick(30.0) == []
