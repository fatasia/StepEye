"""区域编辑后端:YAML 写回、工位热重载、zones 端点校验。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from visual.server import StationManager, build_app, rewrite_station_zones
from visual.dsl import load_config

CFG = Path(__file__).resolve().parents[1] / "configs" / "demo_sim.yaml"


def _fast_cfg_text() -> str:
    text = CFG.read_text(encoding="utf-8")
    return text.replace("realtime: true", "realtime: false").replace("loop: true", "loop: false")


def test_rewrite_station_zones_preserves_other_keys(tmp_path):
    p = tmp_path / "st.yaml"
    p.write_text(_fast_cfg_text(), encoding="utf-8")
    new_zones = [{"id": "bin_harness", "polygon": [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.1]]}]
    rewrite_station_zones(p, new_zones)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["zones"] == new_zones
    assert raw["station"] == "DEMO-ST01" and raw["cycle"]["takt_s"] == 14  # 其余键原样


def test_manager_reload_rebuilds_runtime(tmp_path):
    cfg_path = tmp_path / "st.yaml"
    cfg_path.write_text(_fast_cfg_text(), encoding="utf-8")
    m = StationManager([cfg_path], tmp_path / "data", hub=type("H", (), {"publish": staticmethod(lambda m: None)})())
    old = m.runtimes["DEMO-ST01"]
    m.reload("DEMO-ST01")
    assert m.runtimes["DEMO-ST01"] is not old
    old.stop()
    m.stop()  # reload 启动了采集线程，必须 join 后才能关闭共享数据库


def test_zones_endpoint_validates_writes_and_reloads(tmp_path):
    cfg_path = tmp_path / "st.yaml"
    cfg_path.write_text(_fast_cfg_text(), encoding="utf-8")
    with TestClient(build_app([cfg_path], tmp_path / "data")) as client:
        assert client.get("/api/health").json()["stations"] == 1
        new_zones = [{"id": "bin_harness", "polygon": [[0.02, 0.02], [0.12, 0.02], [0.12, 0.12], [0.02, 0.12]]}] + [
            {"id": zid, "polygon": [[0.5, 0.5], [0.6, 0.5], [0.6, 0.6], [0.5, 0.6]]}
            for zid in ("fixture", "connector_J1", "B1", "B2", "label_zone")]
        r = client.post("/api/stations/DEMO-ST01/zones", json={"zones": new_zones})
        assert r.status_code == 200, r.text
        deadline = time.time() + 15
        while time.time() < deadline:  # 热重载后新配置可见
            cfg = client.get("/api/stations/DEMO-ST01/config").json()
            if cfg["zones"][0]["polygon"][0] == [0.02, 0.02]:
                break
            time.sleep(0.3)
        assert cfg["zones"][0]["polygon"] == [[0.02, 0.02], [0.12, 0.02], [0.12, 0.12], [0.02, 0.12]]
        # 非法输入逐一拒绝,且不污染已有配置
        before = client.get("/api/stations/DEMO-ST01/config").json()["yaml"]
        bad = [
            {"zones": []},
            {"zones": [{"id": "a", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}, {"id": "a", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]},
            {"zones": [{"id": "a", "polygon": [[0, 0], [1, 0], [1, 1]]}]},  # 3 点
            {"zones": [{"id": "a", "polygon": [[0, 0], [1, 0], [1, 1.2], [0, 1]]}]},  # 越界
            {"zones": [{"id": "fixture", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]},  # roi_states 引用断裂
        ]
        for b in bad:
            r = client.post("/api/stations/DEMO-ST01/zones", json=b)
            assert r.status_code == 400, f"{b} 应被拒绝: {r.text}"
            assert client.get("/api/stations/DEMO-ST01/config").json()["yaml"] == before  # 原配置未被破坏
