from __future__ import annotations

from pathlib import Path

import pytest

from visual.dsl import load_config
from visual.runtime import StationRuntime

CFG = Path(__file__).resolve().parents[1] / "configs" / "demo_sim.yaml"


@pytest.fixture()
def results(tmp_path):
    cfg = load_config(CFG)
    cfg.source.realtime = False
    cfg.source.loop = False
    cfg.source.scenarios = ["ok", "missing", "wrong_order"]
    rt = StationRuntime(cfg, tmp_path)
    ended, alerts = [], []
    rt.listeners.append(lambda k, p: ended.append(p) if k == "cycle_end" else alerts.append(p) if k == "violation" else None)
    rt.run()
    yield ended, alerts, tmp_path, rt
    rt.close()


def test_sim_three_cycles_pass_missing_wrong_order(results):
    ended, alerts, _, _ = results
    assert [r["result"] for r in ended] == ["PASS", "FAIL", "FAIL"]
    assert not ended[0]["violations"]
    assert [(v["kind"], v["step"]) for v in ended[1]["violations"]] == [("missing_step", "S4")]
    assert [v["kind"] for v in ended[2]["violations"]] == ["wrong_order"]
    assert all(s["status"] == "done" for s in ended[0]["steps"])
    # 节拍拆解：每步都有耗时
    assert all(s["duration_s"] is not None for s in ended[0]["steps"])


def test_evidence_package_written_with_hash(results):
    ended, _, tmp, rt = results
    cid = ended[1]["cycle_id"]
    ev = tmp / "evidence" / cid / "evidence.json"
    assert ev.exists()
    assert any(f.suffix == ".jpg" for f in (tmp / "evidence" / cid / "frames").iterdir())
    rec = rt.store.get(cid)
    assert rec["sha256"] and rec["result"] == "FAIL" and rec["frames"]
    assert rt.store.stats()["total"] == 3
