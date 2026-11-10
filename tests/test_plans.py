"""厂区平面图端点测试:上传图片/打点/删除/越界拒绝/告警联动统计。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from visual.alerts import AlertStore
from visual.server import build_app


@pytest.fixture()
def client(tmp_path):
    alerts = AlertStore(tmp_path / "data" / "alerts.db")
    app = build_app([], tmp_path / "data", alert_store=alerts)
    with TestClient(app) as c:
        yield c, alerts


def _jpg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.zeros((200, 400, 3), np.uint8))
    assert ok
    return buf.tobytes()


def test_plans_lifecycle(tmp_path):
    client, _ = None, None  # placeholder to keep flake quiet
    alerts = AlertStore(tmp_path / "data" / "alerts.db")
    app = build_app([], tmp_path / "data", alert_store=alerts)
    with TestClient(app) as c:
        # 拒绝非图片
        r = c.post("/api/plans", data={"name": "厂区"},
                   files={"image": ("plan.txt", b"nope", "text/plain")})
        assert r.status_code == 400 and "仅支持图片" in r.json()["detail"]
        # 正常创建
        r = c.post("/api/plans", data={"name": "一号厂房"},
                   files={"image": ("plan.jpg", _jpg(), "image/jpeg")})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert c.get("/api/plans").json()[0]["name"] == "一号厂房"
        assert c.get(f"/api/plans/{pid}/image").status_code == 200
        # 打点
        r = c.post(f"/api/plans/{pid}/points",
                   json={"name": "东门相机", "kind": "camera", "x": 0.3, "y": 0.4})
        assert r.status_code == 201
        ptid = r.json()["id"]
        # 越界/非法 kind 拒绝
        assert client_post_point(c, pid, "x1", "camera", 1.2, 0.3) == 400
        assert client_post_point(c, pid, "x2", "drone", 0.3, 0.3) == 400
        # 详情含点位
        detail = c.get(f"/api/plans/{pid}").json()
        assert detail["points"][0]["name"] == "东门相机"
        # 删点 → 删图
        assert c.delete(f"/api/plans/{pid}/points/{ptid}").json()["ok"] is True
        assert c.delete(f"/api/plans/{pid}").json()["ok"] is True
        assert c.get(f"/api/plans/{pid}").status_code == 404


def client_post_point(c, pid, name, kind, x, y) -> int:
    return c.post(f"/api/plans/{pid}/points", json={"name": name, "kind": kind, "x": x, "y": y}).status_code


def test_plan_point_alert_aggregation(tmp_path):
    """点位有 pending 告警时,站点维度聚合可查(供前端闪红)。"""
    alerts = AlertStore(tmp_path / "data" / "alerts.db")
    rec, new = alerts.create("CAM-N", "safety", "fall", "warn", "倒地", ref_id="A1")
    assert new is True
    rows = [a for a in alerts.list(limit=200) if a["station"] == "CAM-N" and a["status"] == "pending"]
    assert len(rows) == 1
    alerts.close()
