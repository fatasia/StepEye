"""GIS 定位测试:CRUD/越界拒绝/地图聚合端点。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from visual.geo import GeoStore
from visual.server import build_app


def test_geo_store_crud_and_bounds(tmp_path):
    s = GeoStore(tmp_path / "geo.db")
    rec = s.upsert("ST01", "东门岗", "station", 31.23, 121.47)
    assert rec["lat"] == 31.23 and rec["kind"] == "station"
    rec = s.upsert("ST01", "东门岗(新)", "station", 31.24, 121.48)  # upsert 更新
    assert rec["name"].endswith("(新)") and rec["lng"] == 121.48
    assert len(s.list(kind="station")) == 1
    with pytest.raises(ValueError, match="越界"):
        s.upsert("BAD", "bad", "station", 95.0, 200.0)
    assert s.delete("ST01") is True and s.get("ST01") is None
    s.close()


def test_geo_map_endpoint_and_alert_counts(tmp_path):
    from visual.alerts import AlertStore
    alerts = AlertStore(tmp_path / "data" / "alerts.db")
    rec, _ = alerts.create("CAM-E", "safety", "fall", "warn", "倒地", ref_id="A1")
    # 不 close:同一个实例直接注入 app,保持打开;测试结束时随 app 一起关
    app = build_app([], tmp_path / "data", alert_store=alerts)
    with TestClient(app) as c:
        assert c.post("/api/geo", json={"id": "CAM-E", "name": "东门相机", "kind": "camera",
                                        "lat": 31.0, "lng": 121.0}).status_code == 200
        m = c.get("/api/geo/map").json()
        assert m["points"][0]["pending_alerts"] == 1  # 该点位有待处理告警
        assert c.get("/api/geo", params={"kind": "camera"}).json()[0]["id"] == "CAM-E"
        assert c.delete("/api/geo/CAM-E").json()["ok"] is True
        assert c.get("/api/geo/map").json()["points"] == []
        alerts.close()
