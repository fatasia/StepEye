import math
import pytest
from visual.detector_registry import Detection
from visual.tracking import ClassAwareIoUTracker


def d(x=0, label='person', score=.9):
    return Detection(label, (x, 0, x+10, 10), score)


def test_identity_survives_order_change_and_short_gap():
    t = ClassAwareIoUTracker()
    a = t.update([d(0), d(30)], 1)
    assert [v.track_id for v in t.update([d(31), d(1)], 1.1)] == [v.track_id for v in a]
    assert t.update([], 1.2) == []
    b = t.update([d(2), d(32)], 1.3)
    assert [v.track_id for v in b] == [v.track_id for v in a]
    assert all(v.observations == 3 for v in b)


def test_class_isolation_and_one_to_one():
    t = ClassAwareIoUTracker()
    a = t.update([d()], 0)[0]
    b = t.update([d(label='forklift'), d(), d(1)], .1)
    assert len({v.track_id for v in b}) == 3
    assert next(v for v in b if v.label == 'forklift').track_id != a.track_id
    assert sum(v.track_id == a.track_id for v in b) == 1


def test_ttl_reset_and_backwards_time_do_not_reuse_ids():
    t = ClassAwareIoUTracker(ttl_s=.5)
    a = t.update([d()], 0)[0]
    b = t.update([d()], 1)[0]
    t.reset()
    c = t.update([d()], 2)[0]
    e = t.update([d()], 0)[0]
    assert len({v.track_id for v in [a,b,c,e]}) == 4


def test_duplicate_timestamp_no_observation_and_bounded_storage():
    t = ClassAwareIoUTracker(max_tracks=2)
    a = t.update([d()], 0)[0]
    assert t.update([d()], 0) == []
    assert t.update([d()], .1)[0].observations == 2
    for i in range(1, 10):
        assert len(t.update([d(i*100), d(i*100+20), d(i*100+40)], i*.1+.1)) == 2
        assert len(t._tracks) <= 2


@pytest.mark.parametrize('box,score', [((0,0,0,1),.5), ((-1,0,1,1),.5), ((0,0,1,1),math.nan), ((0,0,1,1),1.1)])
def test_detection_contract_invalid(box,score):
    with pytest.raises(ValueError): Detection('person',box,score)


def test_invalid_tracker_config_or_clock():
    with pytest.raises(ValueError): ClassAwareIoUTracker(ttl_s=math.nan)
    with pytest.raises(ValueError): ClassAwareIoUTracker(max_tracks=0)
    with pytest.raises(ValueError): ClassAwareIoUTracker().update([],math.nan)


def test_motion_extrapolation_holds_identity_through_gap():
    """匀速目标在短间隙后应保持身份；预测框吸收位移而非依赖框重叠。"""
    t = ClassAwareIoUTracker(min_iou=.5)
    a = t.update([d(0)], 0)[0]
    t.update([d(2)], .1)   # 2px/0.1s 可匹配（IoU .667），速度 = 20/s
    # 跳过一帧（0.2s 间隙）：无外推时原框 IoU=6/16<0.5，预测框 IoU=1
    b = t.update([d(6)], .3)[0]
    assert b.track_id == a.track_id and b.observations == 3


def test_velocity_cap_prevents_runaway():
    """跳变速度被钳制在单框对角线×每秒 2，预测框不会甩出画面。"""
    t = ClassAwareIoUTracker(min_iou=.3)
    a = t.update([d(0)], 0)[0]
    t.update([d(5)], .1)   # 原始速度 50/s 超出钳制上限
    state = t._tracks[a.track_id]
    assert state.velocity is not None
    cap = 2 * math.hypot(10, 10)
    assert math.isclose(math.hypot(state.velocity[0], state.velocity[1]), cap, rel_tol=1e-6)
    # 钳制后的预测位置应接住原身份
    px = 5 + cap * .1
    b = t.update([Detection('person', (px, 0, px + 10, 10), .9)], .2)[0]
    assert b.track_id == a.track_id and b.observations == 3


def test_first_frame_has_no_velocity_static_objects_unchanged():
    """单次观测无速度：静态目标行为与旧版完全一致。"""
    t = ClassAwareIoUTracker()
    a = t.update([d(0)], 0)[0]
    b = t.update([d(1)], .5)[0]
    assert b.track_id == a.track_id and b.observations == 2
