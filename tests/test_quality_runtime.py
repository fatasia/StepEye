"""真实运行时/引擎质量门禁与安全暂停回归；感知输出替身，不依赖模型或相机。"""
from __future__ import annotations

import numpy as np
import pytest

from visual.dsl import Cond, Program, StationConfig
from visual.engine import Engine
from visual.events import OBJ_STATE, OBJECT_COUNT, PERSON_COUNT, PERSON_FALL, QUALITY, SIGNAL, Event
from visual.perception.persons import PersonZoneTracker
from visual.runtime import StationRuntime
from visual.safety import SafetyMonitor, SafetyRule


def image(mean=100):
    noise = np.random.default_rng(32).normal(0, 12, (240, 320))
    gray = np.clip(mean + noise, 0, 255).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


def config(*, quality=True, rules=None, cycle=True, full_zone=False):
    zone = [[0, 0], [1, 0], [1, 1], [0, 1]] if full_zone else [[.05, .05], [.2, .05], [.2, .25], [.05, .25]]
    raw = {
        "station": "QUALITY-LAB", "source": {"type": "sim", "width": 320, "height": 240, "loop": False},
        "zones": [{"id": "post", "polygon": zone}], "hands": {"enabled": False},
        "quality": {"enabled": quality, "every_n": 1, "brightness": [20, 235], "lighting_drift": .3},
        "safety": {"enabled": bool(rules), "rules": rules or []},
    }
    if cycle:
        raw["cycle"] = {"start": {"signal": {"name": "go"}}, "end": {"signal": {"name": "end"}},
                        "uncertain_quality_s": 100, "max_cycle_s": 600}
        raw["steps"] = [{"id": "S1", "evidence": {"all": [{"obj_state": {"zone": "post", "state": "ok"}}]}}]
    return StationConfig.model_validate(raw)


class RoiOutput:
    def __init__(self):
        self.emit = False
        self.resets = 0

    def process(self, frame, ts, ctx):
        return [Event(ts, OBJ_STATE, zone="post", state="ok", source="test_roi")] if self.emit else []

    def reset_observations(self):
        self.resets += 1

    def states(self):
        return {}


class PersonOutput:
    def __init__(self, geoms, count=2):
        self.tracker = PersonZoneTracker(geoms, enter_frames=1, exit_frames=1)
        self.count = count

    def process(self, frame, ts, ctx):
        boxes = [(20., 20., 35., 45.)] * self.count
        ctx.person_boxes += boxes
        return self.tracker.update(boxes, ts)


@pytest.fixture()
def runtime(tmp_path):
    made = []
    def build(**kwargs):
        rt = StationRuntime(config(**kwargs), tmp_path / str(len(made)))
        rt.hands = None
        rt.roi = RoiOutput()
        made.append(rt)
        return rt
    yield build
    for rt in made:
        rt.stop(); rt.close()


def test_one_observed_bad_frame_cannot_advance_visual_step_before_alarm_confirmation(runtime):
    rt = runtime()
    rt.process_frame(0, image())
    rt.engine.process(Event(1, SIGNAL, name="go"))
    rt.roi.emit = True
    rt.process_frame(2, image(170))
    assert not rt.quality.degraded  # 尚未满足连续3坏帧的报警窗口
    assert rt.engine.world.quality_bad
    assert rt.engine.cycle.steps[0].status == "active"
    assert "post" not in rt.engine.world.zone_state and rt.roi.resets == 1
    assert rt.state()["quality"]["observation"] == "bad"
    assert rt.state()["quality"]["degraded"]


def test_persistent_bad_light_and_physical_signals_finish_uncertain_not_pass(runtime):
    rt = runtime()
    log = []
    rt.engine.subscribe(lambda kind, payload: log.append((kind, payload)))
    rt.process_frame(0, image())
    rt.engine.process(Event(1, SIGNAL, name="go"))
    rt.roi.emit = True
    for index in range(40):
        rt.process_frame(2 + index * .1, image(170))
    rt.engine.process(Event(7, SIGNAL, name="sn", value="SN-QUALITY"))
    rt.engine.process(Event(8, SIGNAL, name="end"))
    result = [p for k, p in log if k == "cycle_end"][-1]
    assert result["result"] == "UNCERTAIN" and result["quality_affected"]
    assert result["sn"] == "SN-QUALITY" and result["steps"][0]["status"] != "done"
    assert result["quality_degraded_s"] > 5


def test_unknown_background_is_immediately_blocked(runtime):
    rt = runtime(full_zone=True)
    rt.engine.process(Event(0, SIGNAL, name="go"))
    rt.roi.emit = True
    rt.process_frame(.1, image())
    assert rt.engine.world.quality_bad and not rt.quality.degraded
    assert rt.engine.cycle.steps[0].status == "active"
    assert rt.state()["quality"]["observation"] == "unknown"


def test_disabled_quality_preserves_existing_visual_behavior(runtime):
    rt = runtime(quality=False)
    rt.engine.process(Event(0, SIGNAL, name="go"))
    rt.roi.emit = True
    raw = image(170); before = raw.copy()
    rt.process_frame(.1, raw)
    assert rt.engine.cycle.steps[0].status == "done" and not rt.engine.world.quality_bad
    assert np.array_equal(raw, before) and rt.state()["quality"]["observation"] == "disabled"


def test_process_generated_safety_alarm_is_not_lost_or_duplicated(runtime):
    rules = [{"id": "crowd", "kind": "person_count", "zone": "post", "min_s": 0, "count_ge": 2}]
    rt = runtime(quality=False, rules=rules, cycle=False)
    rt.persons = PersonOutput(rt.geoms)
    alerts = []
    rt.listeners.append(lambda kind, payload: alerts.append(payload) if kind == "safety" else None)
    rt.process_frame(0, image())
    rt.process_frame(.1, image())
    assert len(alerts) == 1 and alerts[0]["rule"] == "crowd"
    assert rt.state()["meta"]["takt_s"] is None


def test_safety_target_is_copied_from_configuration(runtime):
    rules = [{"id": "phone", "kind": "object_present", "zone": "post", "target": "cell phone", "min_s": 0}]
    rt = runtime(quality=False, rules=rules, cycle=False)
    class Detector:
        def process(self, frame, ts, ctx):
            return [Event(ts, OBJECT_COUNT, zone="post", name="cell phone", value=1)]
    rt.detector = Detector()
    rt.process_frame(0, image())
    assert rt.safety.rules[0].target == "cell phone"
    assert len(rt.alerts) == 1 and rt.alerts[0]["rule"] == "phone"


def test_safety_recovery_requires_fresh_person_observation_and_new_hold_window(runtime):
    rules = [{"id": "crowd", "kind": "person_count", "zone": "post", "min_s": 2, "count_ge": 2}]
    rt = runtime(rules=rules, cycle=False)
    rt.persons = PersonOutput(rt.geoms)
    rt.process_frame(0, image())
    rt.process_frame(10, image(170))
    assert rt.safety.paused and not rt.alerts
    assert rt.safety.tick(90) == []
    rt.process_frame(100, image())
    assert not rt.safety.paused and not rt.alerts
    rt.process_frame(101.9, image())
    assert not rt.alerts
    rt.process_frame(102.1, image())
    assert len(rt.alerts) == 1 and rt.alerts[0]["duration_s"] == pytest.approx(2.1)


def test_safety_pause_discards_old_fall_and_absence_evidence():
    monitor = SafetyMonitor([SafetyRule(id="fall", kind="fall", zone="z", min_s=2),
                             SafetyRule(id="absent", kind="person_absent", zone="z", min_s=2)])
    monitor.process(Event(0, PERSON_FALL, zone="z", value=1))
    monitor.process(Event(0, PERSON_COUNT, zone="z", value=0))
    monitor.pause(1)
    assert monitor.tick(100) == []
    monitor.resume(100)
    assert monitor.tick(200) == []  # 恢复不是恢复旧计时或凭空推断无人
    monitor.process(Event(201, PERSON_FALL, zone="z", value=1))
    assert monitor.tick(202) == []
    assert [a["rule"] for a in monitor.tick(203.1)] == ["fall"]


def test_initial_confirmed_zero_and_reset_zero_are_real_observations(runtime):
    rt = runtime(quality=False, rules=[{"id": "u", "kind": "person_absent", "zone": "post", "min_s": 2}], cycle=False)
    tracker = PersonZoneTracker(rt.geoms, enter_frames=2, exit_frames=2)
    assert tracker.update([], 0) == []
    assert [e.type for e in tracker.update([], .1)] == [PERSON_COUNT]
    tracker.reset()
    assert tracker.update([], 10) == []
    assert tracker.update([], 10.1)[0].value == 0


def test_engine_bad_history_cannot_be_washed_out_after_short_recovery():
    engine = Engine(Program.from_config(config()))
    log = []
    engine.subscribe(lambda kind, payload: log.append((kind, payload)))
    engine.process(Event(1, SIGNAL, name="go"))
    engine.process(Event(1.1, OBJ_STATE, zone="post", state="ok"))
    assert engine.cycle.steps[0].status == "done"
    engine.process(Event(1.2, QUALITY, state="degraded", value="exposure"))
    engine.process(Event(1.21, QUALITY, state="ok"))
    engine.process(Event(1.3, SIGNAL, name="end"))
    result = [p for k, p in log if k == "cycle_end"][-1]
    assert not result["violations"] and result["quality_degraded_s"] < 1
    assert result["result"] == "UNCERTAIN" and result["quality_affected"]


def test_engine_clears_visual_latches_but_keeps_physical_signal_evidence():
    raw = config().model_dump()
    raw["steps"][0]["evidence"] = {"all": [{"obj_state": {"zone": "post", "state": "ok"}},
                                             {"signal": {"name": "torque", "value": "ok"}}]}
    engine = Engine(Program.from_config(StationConfig.model_validate(raw)))
    engine.process(Event(0, SIGNAL, name="go"))
    engine.process(Event(.1, OBJ_STATE, zone="post", state="ok"))
    engine.process(Event(.2, QUALITY, state="unknown", value="background_unavailable"))
    engine.process(Event(.3, OBJ_STATE, zone="post", state="ok"))
    engine.process(Event(.4, SIGNAL, name="torque", value="ok"))
    assert engine.cycle.steps[0].status == "active" and engine.cycle.steps[0].latched == {1}
    engine.process(Event(.5, QUALITY, state="ok"))
    assert engine.cycle.steps[0].status == "active"
    assert not engine._cond_true(Cond(person_count={"zone": "post", "op": "le", "count": 1}), .6)
    engine.process(Event(.7, OBJ_STATE, zone="post", state="ok"))
    assert engine.cycle.steps[0].status == "done"


def test_vlm_result_from_before_bad_frame_is_discarded_after_recovery(runtime):
    rt = runtime()
    rt.process_frame(0, image())
    previous_generation = rt._vision_generation
    rt.process_frame(1, image(170))
    rt._vlm_results.append((previous_generation, "vlm:old", True))
    rt.process_frame(2, image())
    assert "vlm:old" not in rt.engine.world.zone_state


def test_stream_placeholder_invalidates_old_health_between_sampled_frames(runtime):
    rt = runtime()
    rt.quality.cfg.every_n = 5
    for i in range(5):
        rt.quality.process(image(), i / 25, rt.ctx)
    assert rt.quality.snapshot()["observation"] == "ok"
    class Source:
        def __init__(self):
            self.items = iter([(.3, image()), (.4, np.zeros((2, 2, 3), np.uint8)), (.5, image())])
        def read(self):
            return next(self.items, None)
        def close(self):
            pass
    rt.cfg.source.type = "rtsp"
    rt.source = Source()
    generation = rt._vision_generation
    rt.run()
    assert rt.engine.world.quality_bad and rt.engine.world.quality_reason == "stream_gap"
    assert rt.quality.snapshot()["observation"] == "unknown"
    assert rt._vision_generation > generation


def test_obsolete_queued_vlm_frame_is_not_sent_to_provider(runtime):
    rt = runtime()
    rt._vlm_provider = object()  # 若旧代被送判会因没有inspect而失败
    rt._vlm_q.put((rt._vision_generation - 1, "old", "p", b"not-image"))
    rt._vlm_q.put(None)
    rt._vlm_worker()
