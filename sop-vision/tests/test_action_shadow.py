"""动作旁路控制面回归；预测替身不代表识别准确率。"""
import threading
import time

import numpy as np
import pytest

from visual.action_shadow import ActionScheduler, ActionShadow, Window
from visual.dsl import ActionShadowCfg


class Clock:
    now = 0.
    def __call__(self): return self.now


class Runner:
    def __init__(self, *a, **kw): self.closed = False
    def predict(self, clip):
        assert clip.shape == (16, 224, 224, 3)
        return {"label": "take", "confidence": .9, "model_sha256": "a" * 64}
    def close(self): self.closed = True


def until(fn):
    end = time.monotonic() + 3
    while not fn() and time.monotonic() < end: time.sleep(.005)
    assert fn()


def make(tmp_path, factory=Runner, **kwargs):
    model = tmp_path / "action.onnx"
    model.touch()
    clock = Clock()
    scheduler = ActionScheduler(factory, clock=clock)
    cfg = ActionShadowCfg(enabled=True, model=str(model), class_names=["take"], idle_s=.05, **kwargs)
    return ActionShadow(cfg, "s", tmp_path, scheduler, clock), scheduler, clock


FRAME = np.zeros((224, 224, 3), np.uint8)


def feed(owner, clock, count=16, **kw):
    args = dict(generation=1, cycle_id="c1", object_id="p1", quality_ok=True)
    args.update(kw)
    for _ in range(count):
        clock.now += .125
        owner.observe(FRAME, clock.now, **args)


def test_disabled_has_no_worker_or_buffer(tmp_path):
    owner = ActionShadow(ActionShadowCfg(), "s", tmp_path)
    owner.observe(FRAME, 0, generation=1, cycle_id=None, object_id="p", quality_ok=True)
    assert owner.scheduler is None and owner.snapshot()["buffer_frames"] == 0


def test_missing_model_unavailable(tmp_path):
    owner = ActionShadow(ActionShadowCfg(enabled=True, model="missing.onnx", class_names=["x"]), "s", tmp_path)
    assert owner.snapshot()["reason"] == "model_missing"


def test_lazy_start_idle_release_and_restart(tmp_path):
    runners = []
    def factory(*a, **kw):
        runners.append(Runner()); return runners[-1]
    owner, scheduler, clock = make(tmp_path, factory)
    assert scheduler._thread is None
    feed(owner, clock)
    until(lambda: owner._completion is not None)
    feed(owner, clock, 1)
    assert owner.snapshot()["result"]["label"] == "take"
    until(lambda: scheduler._thread is None)
    assert runners[0].closed
    feed(owner, clock, 4)
    until(lambda: len(runners) == 2)
    owner.close()
    until(lambda: scheduler._thread is None)


@pytest.mark.parametrize("change", [dict(generation=2), dict(cycle_id="c2"), dict(object_id="p2"), dict(quality_ok=False), dict(visible=False)])
def test_late_result_cannot_cross_identity_or_quality(tmp_path, change):
    release = threading.Event(); started = threading.Event()
    class Slow(Runner):
        def predict(self, clip):
            started.set(); release.wait(3); return super().predict(clip)
    owner, scheduler, clock = make(tmp_path, Slow)
    try:
        feed(owner, clock); assert started.wait(2)
        feed(owner, clock, 1, **change)
        release.set(); until(lambda: owner._completion is not None)
        feed(owner, clock, 1, **change)
        assert owner.snapshot()["result"] is None
    finally:
        release.set(); owner.close()
        until(lambda: scheduler._thread is None)


def test_expired_and_gap_hide_old_result(tmp_path):
    owner, scheduler, clock = make(tmp_path)
    feed(owner, clock); until(lambda: owner._completion is not None)
    feed(owner, clock, 1)
    assert owner.snapshot()["result"]
    clock.now += 2
    assert owner.snapshot()["result"] is None
    feed(owner, clock, 1)
    assert owner.snapshot()["buffer_frames"] == 1
    owner.close(); until(lambda: scheduler._thread is None)


def test_latest_only_queue_is_bounded(tmp_path):
    release = threading.Event(); started = threading.Event()
    class Slow(Runner):
        def predict(self, clip):
            started.set(); release.wait(3); return super().predict(clip)
    owner, scheduler, clock = make(tmp_path, Slow)
    try:
        feed(owner, clock); assert started.wait(2)
        feed(owner, clock, 20)
        assert len(scheduler._pending) == 1
        assert owner.snapshot()["metrics"]["replaced"] >= 3
        assert len(owner._ring) == 16
    finally:
        release.set(); owner.close(); until(lambda: scheduler._thread is None)


@pytest.mark.parametrize("output", [dict(label="other", confidence=.9, model_sha256="a"*64),
                                    dict(label="take", confidence=float("nan"), model_sha256="a"*64),
                                    dict(label="take", confidence=.1, model_sha256="a"*64)])
def test_bad_or_low_confidence_output_unknown(tmp_path, output):
    class Bad(Runner):
        def predict(self, clip): return output
    owner, scheduler, clock = make(tmp_path, Bad)
    feed(owner, clock); until(lambda: owner._completion is not None)
    feed(owner, clock, 1)
    assert owner.snapshot()["result"] is None
    owner.close(); until(lambda: scheduler._thread is None)


def test_outside_step_window_does_not_start_worker(tmp_path):
    owner, scheduler, clock = make(tmp_path)
    feed(owner, clock, 20, trigger=False)
    assert scheduler._thread is None
    assert owner.snapshot()["reason"] == "outside_step_window"
    owner.close()


def test_runtime_identity_continuity_and_shadow_only(tmp_path):
    from test_quality_runtime import config
    from visual.runtime import StationRuntime
    cfg = config(quality=False)
    cfg.action_shadow.enabled = True
    rt = StationRuntime(cfg, tmp_path)
    calls = []
    rt.action_shadow.observe = lambda *a, **kw: calls.append(kw)
    before = rt.engine.snapshot()
    try:
        rt.ctx.person_boxes = [(0., 0., 100., 100.)]
        rt._action_observe(1, FRAME, False)
        rt._action_observe(2, FRAME, False)
        assert calls[0]["object_id"] == calls[1]["object_id"]
        rt.ctx.person_boxes = [(200., 0., 300., 100.)]
        rt._action_observe(3, FRAME, False)
        assert calls[-1]["object_id"] != calls[0]["object_id"]
        rt.ctx.person_boxes.append((0., 0., 100., 100.))
        rt._action_observe(4, FRAME, False)
        assert calls[-1]["object_id"] is None
        assert rt.engine.snapshot() == before
    finally: rt.close()


def test_vlm_no_idle_thread_and_no_unused_jpeg(tmp_path, monkeypatch):
    from test_vlm_check import _cfg_dict
    from visual.dsl import load_config
    from visual.runtime import StationRuntime
    from visual.events import Event, OBJ_STATE
    cfg = load_config(_cfg_dict(tmp_path, {"all": [{"vlm_check": {"prompt": "connected"}}]}))
    rt = StationRuntime(cfg, tmp_path / "run")
    class Provider:
        def inspect(self, *args): return {"ok": True, "verdict": "no"}
    rt._vlm_provider = Provider()
    assert rt._vlm_thread is None
    count = []
    monkeypatch.setattr("visual.runtime.cv2.imencode", lambda *args: (count.append(1) or True, np.array([1], dtype=np.uint8)))
    try:
        rt._vlm_drain(0, FRAME)
        assert not count
        rt.engine.process(Event(1, OBJ_STATE, zone="fixture", state="present"))
        rt._vlm_drain(2, FRAME)
        until(lambda: rt._vlm_thread is None)
        assert len(count) == 1
        rt._vlm_drain(2.01, FRAME)
        assert len(count) == 1
    finally: rt.close()


def test_model_failure_releases_runner_and_returns_unknown(tmp_path):
    made = []
    class Broken(Runner):
        def __init__(self, *a, **kw): super().__init__(); made.append(self)
        def predict(self, clip): raise RuntimeError("inference failed")
    owner, scheduler, clock = make(tmp_path, Broken)
    feed(owner, clock); until(lambda: owner._completion is not None)
    feed(owner, clock, 1)
    assert owner.snapshot()["reason"] == "model_error"
    assert made[0].closed
    owner.close(); until(lambda: scheduler._thread is None)


@pytest.mark.parametrize("kwargs", [dict(roi=(0,0,0,1)), dict(roi=(0,0,float("nan"),1)),
                                    dict(class_names=["x", "x"]), dict(ttl_ms=0), dict(idle_s=0)])
def test_config_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError): ActionShadowCfg(**kwargs)


def test_shared_scheduler_serializes_stations_without_starvation(tmp_path):
    started = threading.Event(); release = threading.Event(); active = []; runs = []
    class Slow(Runner):
        def predict(self, clip):
            assert not active
            active.append(True); runs.append(1)
            if len(runs) == 1: started.set(); release.wait(3)
            active.clear(); return super().predict(clip)
    first, scheduler, clock = make(tmp_path, Slow, ttl_ms=10000)
    second = ActionShadow(first.cfg, "s2", tmp_path, scheduler, clock)
    try:
        feed(first, clock); assert started.wait(2)
        feed(second, clock)
        feed(first, clock, 16)
        assert list(scheduler._pending) == [second.key, first.key]
        release.set()
        until(lambda: len(runs) >= 2)
    finally:
        release.set(); first.close(); second.close(); until(lambda: scheduler._thread is None)


def test_leaving_trigger_window_cancels_pending_and_invalidates_inflight(tmp_path):
    release = threading.Event(); started = threading.Event()
    class Slow(Runner):
        def predict(self, clip):
            started.set(); release.wait(3); return super().predict(clip)
    owner, scheduler, clock = make(tmp_path, Slow)
    try:
        feed(owner, clock); assert started.wait(2)
        feed(owner, clock, 4)  # second window pending behind first
        assert owner.key in scheduler._pending
        feed(owner, clock, 1, trigger=False)
        assert owner.key not in scheduler._pending
        assert owner.snapshot()["pending"] is False
        assert owner.snapshot()["buffer_frames"] == 16  # preserve pre-trigger cache
        release.set(); until(lambda: owner._completion is not None)
        feed(owner, clock, 1, trigger=True)
        assert owner.snapshot()["result"] is None
        assert owner.snapshot()["metrics"]["discarded"] == 1
    finally:
        release.set(); owner.close(); until(lambda: scheduler._thread is None)

def test_scheduler_passes_isolated_python(tmp_path, monkeypatch):
    monkeypatch.setenv('STEPEYE_ACTION_PYTHON', '/isolated/python')
    options = []
    def factory(*a, **kw):
        options.append(kw)
        return Runner()
    owner, scheduler, clock = make(tmp_path, factory)
    feed(owner, clock)
    until(lambda: owner._completion is not None)
    assert options[0]['python_executable'] == '/isolated/python'
    owner.close()
    until(lambda: scheduler._thread is None)
