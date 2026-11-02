from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from visual.server import build_app

CFG = Path(__file__).resolve().parents[1] / "configs" / "demo_sim.yaml"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    cfg = tmp / "st.yaml"
    # 离线加速：不按实时节奏播放、不循环，三个周期跑完即停
    text = CFG.read_text(encoding="utf-8").replace("realtime: true", "realtime: false").replace("loop: true", "loop: false")
    text = text.replace("scenarios: [ok, missing, ok, wrong_order]", "scenarios: [ok, missing, wrong_order]")
    cfg.write_text(text, encoding="utf-8")
    with TestClient(build_app([cfg], tmp / "data")) as c:
        deadline = time.time() + 90
        while time.time() < deadline and len(c.get("/api/cycles").json()) < 3:
            time.sleep(0.5)
        assert len(c.get("/api/cycles").json()) == 3, "仿真周期未在预期时间内产生"
        yield c


def test_health_and_stations(client):
    assert client.get("/api/health").json() == {"ok": True, "stations": 1}
    st = client.get("/api/stations").json()[0]
    assert st["meta"]["id"] == "DEMO-ST01" and st["meta"]["takt_s"] == 14


def test_config_view_describes_rules(client):
    cfg = client.get("/api/stations/DEMO-ST01/config").json()
    assert [s["id"] for s in cfg["steps"]] == ["S1", "S2", "S3.B1", "S3.B2", "S4"]
    assert "手在「bin_harness」停留" in cfg["steps"][0]["conds"][0]
    assert cfg["yaml"].startswith("#") and len(cfg["zones"]) == 6


def test_cycles_filter_and_detail(client):
    all_rows = client.get("/api/cycles").json()
    assert sorted(r["result"] for r in all_rows) == ["FAIL", "FAIL", "PASS"]
    fails = client.get("/api/cycles", params={"result": "FAIL"}).json()
    assert len(fails) == 2
    rec = client.get(f"/api/cycles/{fails[0]['cycle_id']}").json()
    assert rec["sha256"] and rec["frames"]


def test_evidence_frame_served_as_jpeg(client):
    rec = client.get("/api/cycles").json()[0]
    name = rec["frames"][0]["file"].split("/")[-1]
    r = client.get(f"/api/cycles/{rec['cycle_id']}/frames/{name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("cid,name", [("..", "sop.db"), ("x", ".."), ("a%2Fb", "c"), ("DEMO", "nope.jpg")])
def test_evidence_path_cannot_escape(client, cid, name):
    assert client.get(f"/api/cycles/{cid}/frames/{name}").status_code in (400, 404)


def test_unknown_resources_return_404_with_reason(client):
    r = client.get("/api/stations/NOPE/state")
    assert r.status_code == 404 and "NOPE" in r.json()["detail"]
    assert client.get("/api/cycles/NOPE").status_code == 404


def test_calibrate_is_rejected_for_sim_with_actionable_message(client):
    r = client.post("/api/stations/DEMO-ST01/calibrate", json={"zones": None})
    assert r.status_code == 400 and "仿真" in r.json()["detail"]


def test_stats_day_is_validated_and_trend_is_aggregated(client):
    assert client.get("/api/stats", params={"day": "not-a-day"}).status_code == 422
    stats = client.get("/api/stats").json()
    assert stats["total"] == 3 and stats["pass"] == 1
    trend = client.get("/api/trend").json()
    assert sum(b["total"] for b in trend) == 3


def test_snapshot_and_websocket_hello(client):
    r = client.get("/api/stations/DEMO-ST01/snapshot.jpg", params={"raw": "true"})
    assert r.status_code in (200, 503)  # 仿真播完后画面仍保留最后一帧
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["stations"][0]["id"] == "DEMO-ST01"
