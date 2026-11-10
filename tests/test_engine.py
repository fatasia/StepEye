from __future__ import annotations

import pytest
import yaml

from visual.dsl import Program, StationConfig
from visual.engine import Engine
from visual.events import HAND_ENTER, HAND_EXIT, OBJ_STATE, QUALITY, SIGNAL, Event

BASE = """
station: T1
zones:
  - {id: fixture, polygon: [[0.3,0.2],[0.8,0.2],[0.8,0.8],[0.3,0.8]]}
  - {id: bin, polygon: [[0.0,0.2],[0.2,0.2],[0.2,0.5],[0.0,0.5]]}
  - {id: conn, polygon: [[0.4,0.3],[0.5,0.3],[0.5,0.4],[0.4,0.4]]}
  - {id: B1, polygon: [[0.6,0.3],[0.65,0.3],[0.65,0.35],[0.6,0.35]]}
  - {id: B2, polygon: [[0.66,0.3],[0.7,0.3],[0.7,0.35],[0.66,0.35]]}
  - {id: label, polygon: [[0.55,0.55],[0.7,0.55],[0.7,0.7],[0.55,0.7]]}
cycle:
  start: {obj_state: {zone: fixture, state: present, min_ms: 1000}}
  end:   {obj_state: {zone: fixture, state: absent, min_ms: 500}}
  takt_s: 20
  grace_ms: 500
steps:
  - id: S1
    name: pick
    timeout_s: 5
    evidence: {all: [{hand_dwell: {zone: bin, min_ms: 300}}]}
  - id: S2
    name: plug
    evidence: {all: [{obj_state: {zone: conn, state: mated, min_ms: 200}}]}
  - id: S3
    name: bolts
    foreach: {var: h, values: [B1, B2], order: strict}
    evidence: {all: [{obj_state: {zone: "${h}", state: in, min_ms: 100}}]}
  - id: S4
    name: label
    evidence: {all: [{obj_state: {zone: label, state: present, min_ms: 100}}]}
"""


def make(extra: dict | None = None) -> tuple[Engine, list]:
    d = yaml.safe_load(BASE)
    if extra:
        d.update(extra)
    eng = Engine(Program.from_config(StationConfig.model_validate(d)))
    log: list = []
    eng.subscribe(lambda k, p: log.append((k, p)))
    return eng, log


def st(eng: Engine, t: float, zone: str, state: str) -> None:
    eng.process(Event(ts=t, type=OBJ_STATE, zone=zone, state=state))


def hand(eng: Engine, t0: float, t1: float, zone: str) -> None:
    eng.process(Event(ts=t0, type=HAND_ENTER, zone=zone))
    eng.tick(t1)
    eng.process(Event(ts=t1, type=HAND_EXIT, zone=zone))


def start_cycle(eng: Engine, t: float = 0.0) -> float:
    st(eng, t, "fixture", "present")
    eng.tick(t + 1.1)
    assert eng.cycle is not None
    return t + 1.1


def end_cycle(eng: Engine, t: float) -> dict:
    st(eng, t, "fixture", "absent")
    eng.tick(t + 0.6)
    assert eng.cycle is None


def result(log):
    return [p for k, p in log if k == "cycle_end"][-1]


def good_run(eng: Engine, t: float, skip_label: bool = False) -> float:
    hand(eng, t + 0.5, t + 1.2, "bin")
    st(eng, t + 2.0, "conn", "mated")
    eng.tick(t + 2.3)
    st(eng, t + 3.0, "B1", "in")
    eng.tick(t + 3.2)
    st(eng, t + 4.0, "B2", "in")
    eng.tick(t + 4.2)
    if not skip_label:
        st(eng, t + 5.0, "label", "present")
        eng.tick(t + 5.2)
    return t + 6.0


def test_pass_cycle_with_step_timeline():
    eng, log = make()
    t = start_cycle(eng)
    t = good_run(eng, t)
    end_cycle(eng, t)
    r = result(log)
    assert r["result"] == "PASS"
    assert [s["status"] for s in r["steps"]] == ["done"] * 5  # S1, S2, S3.B1, S3.B2, S4
    assert not r["violations"]
    assert r["rules_hash"]


def test_missing_step_fails():
    eng, log = make()
    t = start_cycle(eng)
    t = good_run(eng, t, skip_label=True)
    end_cycle(eng, t)
    r = result(log)
    assert r["result"] == "FAIL"
    assert [(v["kind"], v["step"]) for v in r["violations"]] == [("missing_step", "S4")]


def test_wrong_order_detected_live_and_fails():
    eng, log = make()
    t = start_cycle(eng)
    st(eng, t + 1.0, "label", "present")
    eng.tick(t + 1.5)
    kinds = [p["kind"] for k, p in log if k == "violation"]
    assert kinds == ["wrong_order"]
    t = good_run(eng, t + 2.0)
    end_cycle(eng, t)
    r = result(log)
    assert r["result"] == "FAIL"
    assert [v["kind"] for v in r["violations"]] == ["wrong_order"]  # 只报一次，且最后不再算漏步
    assert r["steps"][-1]["early"] is True


def test_action_evidence_ignores_hand_before_arming():
    eng, log = make()
    eng.process(Event(ts=0.0, type=HAND_ENTER, zone="bin"))
    st(eng, 0.1, "fixture", "present")
    eng.tick(1.2)
    eng.tick(1.4)
    s1 = eng.snapshot()["cycle"]["steps"][0]
    assert s1["status"] == "active"  # 周期开始前就在料盒里的手不算取料
    eng.tick(1.2 + 0.31)
    assert eng.snapshot()["cycle"]["steps"][0]["status"] == "done"


def test_step_timeout_is_warning_only():
    eng, log = make()
    t = start_cycle(eng)
    eng.tick(t + 6)
    assert [p["kind"] for k, p in log if k == "violation"] == ["step_timeout"]
    hand(eng, t + 6.5, t + 7.0, "bin")
    t = good_run(eng, t + 7.0)
    end_cycle(eng, t)
    r = result(log)
    assert r["result"] == "PASS"  # warn 不影响结果
    assert r["violations"][0]["severity"] == "warn"


def test_quality_degraded_downgrades_to_uncertain():
    eng, log = make()
    t = start_cycle(eng)
    eng.process(Event(ts=t + 0.1, type=QUALITY, state="degraded", value="blur"))
    t = good_run(eng, t + 0.2, skip_label=True)
    eng.process(Event(ts=t, type=QUALITY, state="ok"))
    end_cycle(eng, t + 0.1)
    r = result(log)
    assert r["result"] == "UNCERTAIN"
    assert r["quality_degraded_s"] > 1


def test_baseline_state_is_not_wrong_order():
    eng, log = make()
    st(eng, 0.0, "label", "present")  # 来料时标签已存在
    t = start_cycle(eng)
    t = good_run(eng, t, skip_label=True)
    end_cycle(eng, t)
    r = result(log)
    assert r["result"] == "PASS"
    assert not r["violations"]


def test_rearm_after_cycle_end_requires_start_cond_false():
    eng, log = make({"cycle": yaml.safe_load(BASE)["cycle"] | {"end": {"hand_dwell": {"zone": "bin", "min_ms": 100}}}})
    t = start_cycle(eng)
    hand(eng, t + 0.2, t + 0.5, "bin")  # 既完成 S1 又触发结束
    eng.process(Event(ts=t + 0.6, type=HAND_ENTER, zone="bin"))
    eng.tick(t + 0.9)
    assert [k for k, _ in log].count("cycle_start") == 1  # 工件仍在位，不应立即重复开周期


def test_sn_signal_captured():
    eng, log = make()
    t = start_cycle(eng)
    eng.process(Event(ts=t + 0.1, type=SIGNAL, name="sn", value="SN001"))
    t = good_run(eng, t + 0.2)
    end_cycle(eng, t)
    assert result(log)["sn"] == "SN001"


def test_optional_step_does_not_block_successor():
    d = yaml.safe_load(BASE)
    d["steps"][1]["optional"] = True
    eng = Engine(Program.from_config(StationConfig.model_validate(d)))
    p = {s.id: s for s in eng.p.steps}
    assert p["S3.B1"].eff_preds == ["S1"]
    log: list = []
    eng.subscribe(lambda k, pl: log.append((k, pl)))
    t = start_cycle(eng)
    hand(eng, t + 0.5, t + 1.2, "bin")
    st(eng, t + 3.0, "B1", "in")
    st(eng, t + 3.5, "B2", "in")
    st(eng, t + 5.0, "label", "present")
    eng.tick(t + 5.5)
    end_cycle(eng, t + 6.0)
    assert result(log)["result"] == "PASS"


@pytest.mark.parametrize(
    "patch,msg",
    [
        (lambda d: d["steps"][0]["evidence"]["all"][0]["hand_dwell"].update(zone="nope"), "未定义的 zone"),
        (lambda d: d["steps"][1].update(after=["S9"]), "after"),
        (lambda d: d["steps"].append(dict(d["steps"][0])), "重复"),
        (lambda d: d["steps"][0]["evidence"].update(any=d["steps"][0]["evidence"]["all"]), "all / any / seq"),
    ],
)
def test_dsl_validation(patch, msg):
    d = yaml.safe_load(BASE)
    patch(d)
    with pytest.raises(Exception) as e:
        Program.from_config(StationConfig.model_validate(d))
    assert msg in str(e.value)


def test_foreach_expansion_and_group_reference():
    d = yaml.safe_load(BASE)
    d["steps"][3]["after"] = ["S3"]
    prog = Program.from_config(StationConfig.model_validate(d))
    ids = {s.id: s for s in prog.steps}
    assert list(ids) == ["S1", "S2", "S3.B1", "S3.B2", "S4"]
    assert ids["S3.B2"].preds == ["S3.B1"]
    assert ids["S4"].preds == ["S3.B2"]
    assert ids["S3.B2"].evidence.conds[0].obj_state.zone == "B2"
