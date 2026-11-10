from pathlib import Path
import time

import yaml
from fastapi.testclient import TestClient

from visual.dsl import Cond, Program, StationConfig
from visual.engine import Engine
from visual.events import Event, SIGNAL
from visual.server import build_app


def signal(ts, name, value=True, source="plc-a", ttl=10, quality="ok"):
    return Event(ts, SIGNAL, name=name, value=value, source=source,
                 conf=1 if quality == "ok" else 0,
                 data={"valid_until_ts": ts + ttl, "quality": quality})


def engine():
    cfg = StationConfig.model_validate({"station": "FUSION", "zones": [], "source": {"type": "sim"},
        "cycle": {"start": {"signal": {"name": "go"}}, "end": {"signal": {"name": "end"}}},
        "steps": [{"id": "S1", "evidence": {"all": [
            {"signal": {"name": "clamped", "value": True, "source": "plc-a", "hold_ms": 100}},
            {"signal": {"name": "checked", "value": True, "source": "plc-b", "hold_ms": 100}}]}}]})
    e = Engine(Program.from_config(cfg)); e.process(signal(0, "go")); return e


def test_two_sources_must_be_fresh_simultaneously_not_latched():
    e = engine()
    e.process_batch([signal(1, "clamped", ttl=.15)], 1)
    e.tick(1.11)
    assert e.cycle.steps[0].status == "active"
    e.process_batch([signal(2, "checked", source="plc-b")], 2)
    e.tick(2.11)
    assert e.cycle.steps[0].status == "active"  # clamped 已过期，不能锁存旧true
    e.process_batch([signal(3, "clamped")], 3); e.tick(3.11)
    assert e.cycle.steps[0].status == "done"


def test_invalid_final_packet_in_same_frame_never_passes():
    e = engine()
    e.process_batch([signal(1, "clamped"), signal(1, "checked", source="plc-b")], 1)
    e.process_batch([signal(1.2, "clamped"), signal(1.2, "clamped", quality="invalid")], 1.2)
    assert e.cycle.steps[0].status == "active"
    assert "clamped" in e.snapshot()["fusion"]["unknown"]


def test_unspecified_source_disagreement_and_staleness_fail_closed():
    e = engine()
    c = Cond(signal={"name": "measure", "value": True, "hold_ms": 50})
    e.process_batch([signal(1, "measure", True), signal(1, "measure", False, source="plc-b")], 1)
    assert not e._cond_true(c, 1.1)
    assert "measure" in e.snapshot()["fusion"]["conflicts"]
    e.process_batch([signal(2, "measure", True, source="plc-b", ttl=.1)], 2)
    assert e._cond_true(c, 2.06)
    assert not e._cond_true(c, 2.2)  # 一个来源过期不能静默改成只信另一来源


def test_signal_api_queues_without_advancing_engine_clock(tmp_path):
    original = Path(__file__).resolve().parents[1] / "configs/demo_sim.yaml"
    raw = yaml.safe_load(original.read_text(encoding="utf-8")); raw["signal_sources"] = ["plc-a"]
    p = tmp_path / "st.yaml"; p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    app = build_app([p], tmp_path / "data")
    rt = app.state.manager.get("DEMO-ST01")
    with TestClient(app) as c:
        url = "/api/stations/DEMO-ST01/signals"
        payload = {"name": "clamped", "source": "plc-a", "sequence": 1, "observed_at": time.time(), "value": True}
        assert c.post(url, json=payload).status_code == 202
        assert c.post(url, json=payload).json()["status"] == "duplicate"
        assert c.post(url, json={**payload, "value": False}).status_code == 409
        assert c.post(url, json={**payload, "source": "unknown"}).status_code == 403
        legacy = c.post("/api/stations/DEMO-ST01/signal", json={"name": "sn", "value": "LOCAL-TEST"})
        assert legacy.status_code == 202
        assert rt.engine.now < 1_000_000  # 不再被HTTP的epoch时间推进到十亿秒
        deadline = time.time() + 3
        while time.time() < deadline:
            rows = c.get(url + "/audit").json()
            if any(r["state"] == "consumed" for r in rows): break
            time.sleep(.05)
        assert any(r["state"] == "consumed" and r["engine_ts"] < 1_000_000 for r in rows)
