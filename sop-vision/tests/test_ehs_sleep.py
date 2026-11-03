"""睡岗规则:静止超时触发、大幅移动重置、离开区域重置、区域无人不触发。

时间戳全部用二进制精确值(0/.125/.25/...),min_s=.25,避免浮点比较误差。
"""
import pytest

from visual.ehs import EhsCfg, EhsMonitor
from visual.tracking import ObjectTrack
from visual.dsl import Zone
from visual.perception.base import build_geoms


def monitor(**over):
    rule = dict(id='r', kind='sleep_on_post', zone='z', min_s=.25, min_frames=3) | over
    return EhsMonitor(EhsCfg(enabled=True, rules=[rule]),
                      build_geoms([Zone(id='z', polygon=[[0, 0], [1, 0], [1, 1], [0, 1]])], 100, 100))


def track(ts, tid=1, box=(10, 10, 20, 20)):
    # 100x100 帧,默认框中心 (15,15);max_shift_px 默认 8
    return ObjectTrack(tid, 'person', box, .9, 0, ts, 1)


def test_fires_after_still_duration_once():
    m = monitor()
    assert not m.process([track(0)], 0)
    assert not m.process([track(.125)], .125)
    alerts = m.process([track(.25)], .25)
    assert len(alerts) == 1
    assert alerts[0]['kind'] == 'sleep_on_post' and alerts[0]['track_ids'] == [1]
    assert alerts[0]['duration_s'] == .25 and alerts[0]['source'] == 'ehs_tracks'
    assert '睡岗' in alerts[0]['message']
    assert not m.process([track(.375)], .375)  # 持续静止不重复告警
    assert m.snapshot()['rules']['r']['status'] == 'alert'


def test_small_jitter_below_threshold_keeps_accumulating():
    m = monitor()
    assert not m.process([track(0)], 0)
    assert not m.process([track(.125, box=(12, 12, 22, 22))], .125)  # 位移 ~2.8px < 8
    alerts = m.process([track(.25, box=(10, 10, 20, 20))], .25)      # 回到原位,静止未断
    assert len(alerts) == 1


def test_big_movement_resets_timer():
    m = monitor()
    assert not m.process([track(0)], 0)
    assert not m.process([track(.125)], .125)
    assert not m.process([track(.25, box=(50, 10, 60, 20))], .25)  # 位移 40px,计时重置
    assert not m.process([track(.375, box=(50, 10, 60, 20))], .375)
    alerts = m.process([track(.5, box=(50, 10, 60, 20))], .5)      # 重置后满 min_s
    assert len(alerts) == 1 and alerts[0]['duration_s'] == .25


def test_leaving_zone_resets():
    m = monitor()
    m.process([track(0)], 0)
    m.process([track(.125)], .125)
    m.process([], .25)
    assert m.snapshot()['rules']['r']['status'] == 'clear'
    assert not m.process([track(.375)], .375)  # 回到区域:重新累积
    assert not m.process([track(.5)], .5)
    assert len(m.process([track(.625)], .625)) == 1


def test_empty_zone_never_alerts():
    m = monitor()
    for ts in [0, .125, .25, .375, .5]:
        assert not m.process([], ts)
        assert m.snapshot()['rules']['r']['status'] == 'clear'


def test_track_id_switch_restarts_accumulation():
    m = monitor()
    m.process([track(0)], 0)
    m.process([track(.125)], .125)
    assert not m.process([track(.25, tid=2)], .25)  # 新 ID 从零累积
    assert not m.process([track(.375, tid=2)], .375)
    assert len(m.process([track(.5, tid=2)], .5)) == 1


def test_gap_and_unavailable_reset_sleep_state():
    m = monitor()
    m.process([track(0)], 0)
    m.process([track(.125)], .125)
    assert not m.process([track(2)], 2)      # gap > max_gap_s 全量重置
    assert not m.process([track(2.125)], 2.125)
    assert len(m.process([track(2.25)], 2.25)) == 1  # 重置后重新计满 min_s
    assert not m.process([track(2.375)], 2.375)      # 已触发不重复
    m.process([], 2.5, available=False)
    assert m.snapshot()['rules']['r'] == {'status': 'unknown', 'track_ids': []}


def test_unknown_when_labels_unavailable():
    m = monitor()
    assert not m.process([], 0, available_labels={'fire'})
    assert m.snapshot()['rules']['r']['status'] == 'unknown'


def test_config_rejects_count_limits_and_bad_shift():
    with pytest.raises(ValueError): EhsCfg(rules=[dict(id='r', kind='sleep_on_post', zone='z', min_count=1)])
    with pytest.raises(ValueError): EhsCfg(rules=[dict(id='r', kind='sleep_on_post', zone='z', max_count=1)])
    with pytest.raises(ValueError): EhsCfg(rules=[dict(id='r', kind='sleep_on_post', zone='z', max_shift_px=0)])
    cfg = EhsCfg(rules=[dict(id='r', kind='sleep_on_post', zone='z', max_shift_px=15.)])
    assert cfg.rules[0].max_shift_px == 15
