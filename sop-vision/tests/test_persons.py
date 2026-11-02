"""人员感知与安全条件的离线测试:纯逻辑,不依赖 MediaPipe 与模型文件。"""
from __future__ import annotations

from visual.dsl import Cond, CycleDef, PersonAbsent, PersonCount, PersonPresent, Program, SignalCond, Zone
from visual.engine import Engine
from visual.events import Event, PERSON_COUNT, PERSON_ENTER, PERSON_EXIT
from visual.perception.base import build_geoms
from visual.perception.persons import PersonZoneTracker

ZONE = Zone(id="post", polygon=[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]])  # 左半屏


def _tracker() -> PersonZoneTracker:
    return PersonZoneTracker(build_geoms([ZONE], 100, 100), enter_frames=2, exit_frames=2)


def _box_in() -> tuple[float, float, float, float]:
    return (10.0, 40.0, 30.0, 60.0)  # 中心 (20,50) 在左半区


def _box_out() -> tuple[float, float, float, float]:
    return (70.0, 40.0, 90.0, 60.0)


def test_tracker_rise_after_stable_frames():
    t = _tracker()
    assert t.update([_box_out()], 1.0) == []  # 区域外:0 人与初值相同,无事件
    assert t.update([_box_in()], 2.0) == []  # 单帧不稳,先不发
    evs = t.update([_box_in()], 3.0)  # 连续 enter_frames 帧 → 进入
    assert [e.type for e in evs] == [PERSON_ENTER, PERSON_COUNT]
    assert evs[-1].value == 1
    assert t.update([], 3.5) == []  # 掉回 0 但只 1 帧,去抖吞掉


def test_tracker_fall_after_stable_frames_and_flap_suppressed():
    t = _tracker()
    t.update([_box_in()], 1.0)
    evs = t.update([_box_in()], 2.0)
    assert [e.type for e in evs] == [PERSON_ENTER, PERSON_COUNT]
    assert t.update([], 3.0) == []  # 1 帧 0 人:去抖
    evs = t.update([], 4.0)
    assert [e.type for e in evs] == [PERSON_EXIT, PERSON_COUNT] and evs[-1].value == 0


def test_tracker_rise_needs_enter_frames_fall_needs_exit_frames():
    t = PersonZoneTracker(build_geoms([ZONE], 100, 100), enter_frames=3, exit_frames=1)
    assert t.update([_box_in()], 1.0) == []
    assert t.update([_box_in()], 2.0) == []
    evs = t.update([_box_in()], 3.0)  # 第 3 帧稳定 → 进入
    assert [e.type for e in evs] == [PERSON_ENTER, PERSON_COUNT]
    assert t.update([], 4.0) != []  # 退出只需 1 帧
    assert t.count["post"] == 0


def test_tracker_count_two_persons():
    t = _tracker()
    t.update([_box_in()], 1.0)
    t.update([_box_in()], 2.0)  # 稳定到 1 人
    assert t.update([_box_in(), (12.0, 42.0, 32.0, 62.0)], 3.0) == []  # 1→2 单帧不稳
    evs = t.update([_box_in(), (12.0, 42.0, 32.0, 62.0)], 4.0)
    assert [e.type for e in evs] == [PERSON_COUNT] and evs[-1].value == 2  # 只有计数事件,无进出边沿


def _engine() -> Engine:
    prog = Program("ST", [], CycleDef(start=Cond(signal=SignalCond(name="s")), end=Cond(signal=SignalCond(name="s"))),
                   {}, "hash")
    return Engine(prog)


def _feed(engine: Engine, ev_type: str, ts: float, zone: str = "post", value=None) -> None:
    engine.process(Event(ts, ev_type, zone=zone, value=value))


def test_person_present_condition_with_min_ms():
    e = _engine()
    cond = Cond(person_present=PersonPresent(zone="post", min_ms=500))
    _feed(e, PERSON_COUNT, 1.0, value=1)
    assert e._cond_true(cond, 1.4) is False
    assert e._cond_true(cond, 1.6) is True


def test_person_absent_starts_from_first_confirmed_zero():
    e = _engine()
    cond = Cond(person_absent=PersonAbsent(zone="post", min_ms=400))
    assert e._cond_true(cond, 5.0) is False  # 从未确认过人数:不能凭空判"无人"
    _feed(e, PERSON_COUNT, 1.0, value=0)  # 首次确认无人
    assert e._cond_true(cond, 1.3) is False
    assert e._cond_true(cond, 1.5) is True
    _feed(e, PERSON_COUNT, 2.0, value=1)
    assert e._cond_true(cond, 3.0) is False  # 有人了,无人条件立即不成立


def test_person_absent_restarts_after_exit():
    e = _engine()
    cond = Cond(person_absent=PersonAbsent(zone="post", min_ms=300))
    _feed(e, PERSON_ENTER, 1.0)
    assert e._cond_true(cond, 2.0) is False
    _feed(e, PERSON_EXIT, 2.0)  # 无人时钟从此刻重起
    assert e._cond_true(cond, 2.2) is False
    assert e._cond_true(cond, 2.4) is True


def test_person_count_ge_le():
    e = _engine()
    ge = Cond(person_count=PersonCount(zone="post", op="ge", count=2))
    le = Cond(person_count=PersonCount(zone="post", op="le", count=1))
    _feed(e, PERSON_COUNT, 1.0, value=1)
    assert e._cond_true(ge, 1.1) is False and e._cond_true(le, 1.1) is True
    _feed(e, PERSON_COUNT, 2.0, value=3)
    assert e._cond_true(ge, 2.1) is True and e._cond_true(le, 2.1) is False


def test_step_evidence_of_person_absent_fits_workflow():
    """person 条件可当步骤证据(与 hand_dwell 同一求值路径,带 floor 起步时间)。"""
    e = _engine()
    cond = Cond(person_absent=PersonAbsent(zone="post", min_ms=100))
    _feed(e, PERSON_COUNT, 1.0, value=0)
    assert e._cond_true(cond, 1.05, None, floor=1.2) is False  # floor(激活时间)之前的无人时长不算
    assert e._cond_true(cond, 1.35, None, floor=1.2) is True


def test_describe_and_zone_wiring():
    c1 = Cond(person_present=PersonPresent(zone="post", min_ms=200))
    c2 = Cond(person_count=PersonCount(zone="post", op="ge", count=2))
    assert c1.zone == "post" and c2.zone == "post"
    assert "有人持续" in c1.describe() and "≥2" in c2.describe()
