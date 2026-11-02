"""S4 跌倒与通用检测器的纯逻辑测试(不依赖 MediaPipe/模型文件)。"""
from __future__ import annotations

import pytest

from visual.events import OBJECT_COUNT, OBJECT_ENTER, OBJECT_EXIT, PERSON_FALL, PERSON_ENTER, PERSON_EXIT, Event
from visual.perception.base import build_geoms
from visual.perception.objects import ZoneCountTracker
from visual.perception.persons import is_fall_posture
from visual.safety import SafetyMonitor, SafetyRule
from visual.dsl import Zone

ZONE = Zone(id="floor", polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])


def _pose(shoulder_y: float, hip_y: float, dx: float = 0.0, x: float = 0.5, y: float = 0.5) -> list:
    """33 点骨架,只填 11/12 肩与 23/24 髋;x/y 为中心参考点。"""
    pts = [(x, y)] * 33
    pts[11], pts[12] = (x + dx - 0.02, shoulder_y), (x + dx + 0.02, shoulder_y)
    pts[23], pts[24] = (x - 0.02, hip_y), (x + 0.02, hip_y)
    return [(float(a), float(b)) for a, b in pts]


def test_fall_posture_standing_false_lying_true():
    standing = _pose(shoulder_y=0.25, hip_y=0.60)  # 肩在上,躯干竖直
    assert is_fall_posture(standing) is False
    bending = _pose(shoulder_y=0.40, hip_y=0.60)  # 弯腰 ~34°,不报
    assert is_fall_posture(bending) is False
    lying = _pose(shoulder_y=0.50, hip_y=0.52, dx=0.25)  # 躺平:肩髋同高,水平偏移大
    assert is_fall_posture(lying) is True
    flat = _pose(shoulder_y=0.50, hip_y=0.50)  # 完全水平
    assert is_fall_posture(flat) is True
    assert is_fall_posture([(0.1, 0.1)] * 10) is False  # 关键点不足不崩


def test_zone_count_tracker_generic_keys():
    t = ZoneCountTracker(build_geoms([ZONE], 100, 100), enter_frames=2, exit_frames=2)
    phone = ("cell phone", (10.0, 40.0, 30.0, 60.0))
    assert t.update([phone], 1.0) == []  # 单帧不稳
    evs = t.update([phone], 2.0)
    assert [e.type for e in evs] == [OBJECT_ENTER, OBJECT_COUNT] and evs[-1].name == "cell phone"
    assert t.update([], 3.0) == []  # 单帧消失:去抖
    evs = t.update([], 4.0)
    assert [e.type for e in evs] == [OBJECT_EXIT, OBJECT_COUNT]


def test_monitor_fall_rule_fires_once_and_rearms():
    m = SafetyMonitor([SafetyRule(id="fall1", kind="fall", zone="floor", min_s=3.0)])
    assert m.process(Event(1.0, PERSON_FALL, zone="floor", value=1)) == []
    assert m.tick(3.0) == []  # 未到 3s
    alerts = m.tick(4.5)
    assert len(alerts) == 1 and alerts[0]["kind"] == "fall" and "倒地" in alerts[0]["message"]
    assert m.tick(6.0) == []  # 已触发不重复
    m.process(Event(7.0, PERSON_FALL, zone="floor", value=0))  # 恢复:重新武装
    assert m.tick(9.0) == []
    m.process(Event(10.0, PERSON_FALL, zone="floor", value=1))
    assert len(m.tick(13.5)) == 1  # 再次触发


def test_monitor_object_present_rule():
    m = SafetyMonitor([SafetyRule(id="phone", kind="object_present", zone="floor",
                                  target="cell phone", min_s=4.0, severity="error")])
    m.process(Event(1.0, OBJECT_COUNT, zone="floor", name="cell phone", value=1))
    assert m.tick(3.0) == []
    alerts = m.tick(5.5)
    assert len(alerts) == 1 and alerts[0]["severity"] == "error" and "cell phone" in alerts[0]["message"]
    m.process(Event(6.0, OBJECT_COUNT, zone="floor", name="cell phone", value=0))  # 解除
    assert m.tick(9.0) == []
    m.process(Event(10.0, OBJECT_COUNT, zone="floor", name="cell phone", value=1))
    assert len(m.tick(14.5)) == 1


def test_object_rules_isolated_per_class_and_zone():
    m = SafetyMonitor([SafetyRule(id="phone", kind="object_present", zone="floor", target="cell phone", min_s=1.0)])
    m.process(Event(1.0, OBJECT_COUNT, zone="floor", name="knife", value=3))
    m.process(Event(1.0, OBJECT_COUNT, zone="other", name="cell phone", value=5))
    assert m.tick(5.0) == []  # 类别与区域都要对上


def test_describe_covers_new_kinds():
    r = SafetyRule(id="f", kind="fall", zone="floor", min_s=2)
    o = SafetyRule(id="p", kind="object_present", zone="floor", target="cell phone", count_ge=1, min_s=2)
    assert "倒地" in r.describe() and "cell phone" in o.describe()


def test_person_events_still_work_after_object_merge():
    m = SafetyMonitor([SafetyRule(id="u", kind="person_absent", zone="floor", min_s=2.0)])
    m.process(Event(1.0, PERSON_ENTER, zone="floor"))
    assert m.tick(2.0) == []
    m.process(Event(2.0, PERSON_EXIT, zone="floor"))
    assert len(m.tick(4.5)) == 1  # 旧语义无回归
