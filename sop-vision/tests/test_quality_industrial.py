"""画质工程故障路径回归；合成图用于确定性边界，不代表现场精度验收。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from visual.dsl import QualityCfg, Zone
from visual.perception.base import Context, build_geoms
from visual.perception.quality import QualityPerceiver


def cfg(**overrides):
    options = QualityCfg(enabled=True, every_n=1, brightness=(20, 235), lighting_drift=.3).model_dump()
    options.update(bad_confirm=3, good_confirm=5, recovery_ratio=.8, min_background_fraction=.05,
                   min_background_pixels=64, background_texture_min=1., max_dark_fraction=.35,
                   max_bright_fraction=.25, dark_pixel=8, bright_pixel=247, tile_grid=4,
                   max_tile_dark_fraction=.8, max_tile_bright_fraction=.8,
                   tile_lighting_drift=.45, max_drifted_tile_fraction=.15)
    options.update(overrides)
    return SimpleNamespace(**options)


def frame(mean=100, seed=10):
    noise = np.random.default_rng(seed).normal(0, 12, (240, 320))
    gray = np.clip(mean + noise, 0, 255).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


def feed(q, image, count=3, ctx=None, start=0):
    events = []
    for index in range(count):
        events += q.process(image, start + index * .1, ctx or Context({}))
    return events


def test_persistent_day_night_change_never_rewrites_reference():
    q = QualityPerceiver(cfg())
    feed(q, frame(), 3)
    reference = q._ref.copy()
    events = feed(q, frame(170), 150, start=1)
    assert q.degraded and q.reason == "lighting_drift"
    assert len(events) == 1 and events[0].state == "degraded"
    assert np.array_equal(reference, q._ref)
    assert q.metrics["brightness_drift"] > .6
    assert q.snapshot()["reference_source"] == "initial"


def test_short_flash_and_recovery_confirmation_windows():
    q = QualityPerceiver(cfg())
    feed(q, frame(), 3)
    assert feed(q, frame(170), 2) == [] and not q.degraded
    assert feed(q, frame(), 2) == []
    feed(q, frame(170), 3)
    assert q.degraded
    assert feed(q, frame(), 4) == [] and q.degraded
    events = feed(q, frame(), 1)
    assert not q.degraded and events[0].state == "ok"


def test_recovery_threshold_has_hysteresis_not_just_delay():
    q = QualityPerceiver(cfg())
    feed(q, frame(), 3)
    feed(q, frame(135), 3)
    assert q.degraded
    feed(q, frame(125), 12)  # 25% < 进入阈值30%，但高于退出阈值24%
    assert q.degraded
    feed(q, frame(115), 5)
    assert not q.degraded


@pytest.mark.parametrize("value", [0, 255])
def test_local_exposure_is_not_hidden_by_global_average(value):
    q = QualityPerceiver(cfg(lighting_drift=None))
    image = frame()
    image[:60, :80] = value  # 仅占全图1/16；均值仍在正常范围
    assert 20 < image.mean() < 235
    events = feed(q, image)
    assert q.degraded and q.reason == "exposure_local"
    assert events[0].state == "degraded"


def test_non_saturated_local_lighting_change_uses_background_tiles():
    q = QualityPerceiver(cfg())
    base = frame()
    feed(q, base)
    changed = base.copy()
    changed[:120, :160] = np.clip(changed[:120, :160].astype(np.int16) + 65, 0, 255).astype(np.uint8)
    feed(q, changed)
    assert q.degraded and q.reason == "lighting_local"
    assert q.metrics["brightness_drift"] < .3
    assert q.metrics["drifted_tile_fraction"] >= .2


def test_initial_reference_uses_background_not_work_zone():
    zone = Zone(id="work", polygon=[(0, 0), (.75, 0), (.75, 1), (0, 1)])
    ctx = Context(build_geoms([zone], 320, 240))
    q = QualityPerceiver(cfg())
    initial = frame(); initial[:, :240] = 175
    feed(q, initial, ctx=ctx)
    assert 95 < q._light_ref < 105
    changed = initial.copy(); changed[:, :240] = 210
    assert feed(q, changed, 8, ctx=ctx) == []
    assert not q.degraded and q.metrics["brightness_drift"] == pytest.approx(0, abs=1e-6)


def test_all_work_zone_is_unknown_not_zero_mean_normal():
    zone = Zone(id="work", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)])
    ctx = Context(build_geoms([zone], 320, 240))
    q = QualityPerceiver(cfg())
    feed(q, frame(), ctx=ctx)
    assert q.degraded and q.reason == "background_unavailable"
    assert q.metrics["background_mean"] is None and q.metrics["background_pixels"] == 0
    assert q.snapshot()["observation"] == "unknown" and not q.snapshot()["reference_ready"]
    json.dumps(q.snapshot(), allow_nan=False)


def test_foreground_occlusion_does_not_turn_unknown_into_ok():
    q = QualityPerceiver(cfg())
    feed(q, frame())
    ctx = Context({}); ctx.person_boxes = [(0, 0, 320, 240)]
    feed(q, frame(140), ctx=ctx)
    assert q.degraded and q.snapshot()["observation"] == "unknown"
    assert q.reason == "background_unavailable"
    feed(q, frame(), 5)
    assert not q.degraded


def test_textureless_background_with_shift_monitor_is_unknown():
    q = QualityPerceiver(cfg(max_shift_px=5))
    feed(q, np.full((240, 320, 3), 100, np.uint8))
    assert q.degraded and q.reason == "texture_insufficient"
    assert q.snapshot()["observation"] == "unknown"


def test_manual_recalibration_accepts_changed_good_light_then_confirms_recovery():
    q = QualityPerceiver(cfg())
    feed(q, frame())
    feed(q, frame(170))
    assert q.degraded and q.set_reference(frame(170), Context({}))
    assert q.degraded  # 人工换参考也要走恢复确认窗口
    feed(q, frame(170), 4)
    assert q.degraded
    feed(q, frame(170), 1)
    assert not q.degraded and q.snapshot()["reference_source"] == "manual"


def test_reference_reset_never_auto_bootstraps_from_next_frame():
    q = QualityPerceiver(cfg())
    feed(q, frame())
    q.reset_reference()
    feed(q, frame(170), 100)
    assert q.degraded and q.reason == "reference_unavailable" and q._ref is None
    assert q.set_reference(frame(170), Context({}))
    feed(q, frame(170), 5)
    assert not q.degraded


def test_reject_bad_reference_without_overwriting_previous_baseline():
    q = QualityPerceiver(cfg())
    feed(q, frame())
    reference = q._ref.copy()
    assert not q.set_reference(np.zeros((240, 320, 3), np.uint8), Context({}))
    assert q.snapshot()["reference_rejected"] == "exposure"
    assert np.array_equal(q._ref, reference)


def test_invalid_frame_reports_serializable_unknown_instead_of_crashing():
    q = QualityPerceiver(cfg())
    feed(q, np.zeros((240, 320), np.uint8))
    assert q.degraded and q.reason == "invalid_frame"
    json.dumps(q.snapshot(), allow_nan=False)


def test_camera_shift_and_phase_correlation_do_not_mutate_fixed_reference():
    q = QualityPerceiver(cfg(lighting_drift=None, max_shift_px=3))
    image = frame(seed=88)
    feed(q, image)
    baseline, original = q._ref.copy(), image.copy()
    for _ in range(5):
        assert not q.process(image, 1, Context({}))
    assert np.array_equal(baseline, q._ref) and np.array_equal(original, image)
    shifted = cv2.warpAffine(image, np.array([[1, 0, 10], [0, 1, 0]], np.float32), (320, 240), borderMode=cv2.BORDER_REFLECT)
    feed(q, shifted)
    assert q.degraded and q.reason == "camera_shift"
    assert 7 < q.metrics["shift_px"] < 13 and q.metrics["shift_response"] >= .5


def test_every_n_sampling_and_serializable_metric_snapshot():
    q = QualityPerceiver(cfg(every_n=3))
    feed(q, frame(), 3)
    feed(q, frame(170), 8)
    assert not q.degraded
    events = feed(q, frame(170), 1, start=2)
    assert q.degraded and len(events) == 1
    out = q.snapshot()
    assert out["sample_ts"] == 2 and out["background_pixels"] > 0
    json.dumps(out, allow_nan=False)
