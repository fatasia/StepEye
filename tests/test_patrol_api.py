"""巡更 API 接入：角色权限、区域归属、计划生命周期与无观测语义。"""
from pathlib import Path

from fastapi.testclient import TestClient

from visual.auth import AuthStore
from visual.server import build_app


def test_patrol_api_roles_and_plan_lifecycle(tmp_path):
    cfg = Path(__file__).resolve().parents[1] / "configs/demo_sim.yaml"
    auth = AuthStore(tmp_path / "auth.db")
    auth.set_user("op", "password123", "operator")
    auth.set_user("reader", "password123", "viewer")
    app = build_app([cfg], tmp_path / "data", auth=auth)
    body = {"name": "早巡", "station": "DEMO-ST01", "zone": "bin_harness",
            "start": "08:00", "end": "08:01", "min_presence_s": 10}
    with TestClient(app) as c:
        assert c.get("/api/patrol/plans").status_code == 401
        assert c.post("/api/patrol/plans", json=body).status_code == 401
        c.post("/api/auth/login", json={"username": "reader", "password": "password123"})
        assert c.get("/api/patrol/plans").json() == []
        assert c.post("/api/patrol/plans", json=body).status_code == 403
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"username": "op", "password": "password123"})
        bad = {**body, "zone": "not-a-zone"}
        assert c.post("/api/patrol/plans", json=bad).status_code == 400
        assert c.post("/api/patrol/plans", json={**body, "station": "NOPE"}).status_code == 404
        assert c.post("/api/patrol/plans", json={**body, "min_presence_s": 61}).status_code == 422
        res = c.post("/api/patrol/plans", json=body)
        assert res.status_code == 201
        pid = res.json()["id"]
        assert c.get("/api/patrol/plans").json()[0]["id"] == pid
        assert c.get("/api/patrol/report", params={"day": "bad"}).status_code == 400
        assert c.get("/api/patrol/report", params={"day": "20261103"}).status_code == 400
        report = c.get("/api/patrol/report", params={"day": "2099-11-03"}).json()
        assert report["timezone"] == "UTC+08:00" and report["rows"][0]["status"] == "pending"
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"username": "reader", "password": "password123"})
        assert c.delete(f"/api/patrol/plans/{pid}").status_code == 403
        c.post("/api/auth/logout")
        c.post("/api/auth/login", json={"username": "op", "password": "password123"})
        assert c.delete(f"/api/patrol/plans/{pid}").status_code == 200
        assert c.delete(f"/api/patrol/plans/{pid}").status_code == 404
        assert c.get("/api/patrol/plans").json() == []
    auth.close()
