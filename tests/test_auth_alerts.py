"""认证(T5)与告警中心(V7)测试:纯逻辑 + API 鉴权矩阵。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from visual.alerts import AlertStore
from visual.auth import AuthError, AuthStore
from visual.server import StationManager, build_app

CFG = Path(__file__).resolve().parents[1] / "configs" / "demo_sim.yaml"


def _fast_cfg_text() -> str:
    text = CFG.read_text(encoding="utf-8")
    return text.replace("realtime: true", "realtime: false").replace("loop: true", "loop: false")


# ---------------- AuthStore 纯逻辑 ----------------
def test_auth_user_lifecycle(tmp_path):
    a = AuthStore(tmp_path / "auth.db")
    with pytest.raises(ValueError, match="至少 8 位"):
        a.set_user("u1", "short", "operator")
    a.set_user("admin1", "password123", "admin")
    a.set_user("op1", "password123", "operator")
    assert a.count() == 2 and a.list_users()[0]["role"] == "admin"
    with pytest.raises(AuthError):
        a.login("admin1", "wrong-password")
    token = a.login("admin1", "password123")
    assert a.verify_session(token)["role"] == "admin"
    a.logout(token)
    assert a.verify_session(token) is None
    assert a.verify_session("garbage") is None
    assert a.delete_user("op1") is True and a.delete_user("op1") is False


def test_auth_check_roles_ranked():
    AuthStore.check({"username": "a", "role": "viewer"}, "viewer")
    with pytest.raises(AuthError, match="operator"):
        AuthStore.check({"username": "a", "role": "viewer"}, "operator")
    with pytest.raises(AuthError, match="未登录"):
        AuthStore.check(None, "viewer")
    AuthStore.check({"username": "a", "role": "admin"}, "operator")


# ---------------- AlertStore 纯逻辑 ----------------
def test_alert_dedup_never_merges_different_stations(tmp_path):
    store = AlertStore(tmp_path / 'alerts.db')
    try:
        first, _ = store.create('station-a', 'sop', 'missing_step', 'error', '缺件', ref_id='same-cycle')
        second, created = store.create('station-b', 'sop', 'missing_step', 'error', '缺件', ref_id='same-cycle')
        assert created and first['id'] != second['id']
    finally:
        store.close()


def test_alert_create_dedup_and_update_flow(tmp_path):
    s = AlertStore(tmp_path / "alerts.db")
    rec1, new1 = s.create("ST", "sop", "missing_step", "error", "漏步", ref_id="C1:missing_step")
    assert new1 is True and rec1["status"] == "pending"
    rec2, new2 = s.create("ST", "sop", "missing_step", "error", "漏步", ref_id="C1:missing_step")
    assert new2 is False and rec2["id"] == rec1["id"]  # 防重:回写不新建
    upd = s.update(rec1["id"], status="done", comment="已补装", handler="张三")
    assert upd["status"] == "done" and upd["handler"] == "张三"
    with pytest.raises(ValueError, match="状态"):
        s.update(rec1["id"], status="bogus")
    # 已处理后再来同 ref → 新告警(状态机重开)
    _, new3 = s.create("ST", "sop", "missing_step", "error", "漏步", ref_id="C1:missing_step")
    assert new3 is True
    st = s.stats()
    assert st["total"] == 2 and st["done"] == 1 and st["pending"] == 1
    assert [a["id"] for a in s.list(status="pending")] == [2]
    s.close()


def test_manager_forwarder_collects_violations_and_safety(tmp_path):
    cfg_path = tmp_path / "st.yaml"
    cfg_path.write_text(_fast_cfg_text(), encoding="utf-8")
    store = AlertStore(tmp_path / "alerts.db")
    m = StationManager([cfg_path], tmp_path / "data",
                       hub=type("H", (), {"publish": staticmethod(lambda msg: None)})(),
                       alert_store=store)
    fwd = m._forwarder("DEMO-ST01")
    payload = {"cycle_id": "C1", "kind": "missing_step", "severity": "error", "message": "漏步:贴标签", "ts": 1.0}
    fwd("violation", payload)
    fwd("violation", payload)  # 同周期同类:防重
    assert len(store.list(source="sop")) == 1
    fwd("safety", {"rule": "unattended", "severity": "warn", "message": "安全规则触发:无人值守", "ts": 2.0})
    fwd("safety", {"rule": "unattended", "severity": "warn", "message": "x", "ts": 3.0})  # safety 不防重(再武装语义)
    assert len(store.list(source="safety")) == 2
    assert store.stats()["total"] == 3
    m.runtimes["DEMO-ST01"].stop()
    store.close()
    m.store.close()
    m.alerts.close()


# ---------------- API 鉴权矩阵 ----------------
@pytest.fixture()
def auth_app(tmp_path):
    cfg_path = tmp_path / "st.yaml"
    cfg_path.write_text(_fast_cfg_text(), encoding="utf-8")
    auth = AuthStore(tmp_path / "auth.db")
    auth.set_user("admin1", "password123", "admin")
    auth.set_user("op1", "password123", "operator")
    auth.set_user("view1", "password123", "viewer")
    alerts = AlertStore(tmp_path / "alerts.db")
    app = build_app([cfg_path], tmp_path / "data", auth=auth, alert_store=alerts)
    return TestClient(app), auth


def _login(client, who):
    r = client.post("/api/auth/login", json={"username": who, "password": "password123"})
    assert r.status_code == 200
    return r


def test_auth_matrix_on_api(auth_app):
    client, _ = auth_app
    assert client.get("/api/health").json()["auth"] is True  # 白名单放行
    assert client.get("/api/stations").status_code == 401  # 未登录
    assert client.post("/api/auth/login", json={"username": "x", "password": "wrongyy"}).status_code == 401
    _login(client, "view1")
    assert client.get("/api/stations").status_code == 200          # viewer 可读
    assert client.get("/api/users").status_code == 403             # viewer 不可管用户
    assert client.post("/api/stations/DEMO-ST01/signal", json={"name": "sn", "value": "A"}).status_code == 403
    _login(client, "op1")
    assert client.post("/api/stations/DEMO-ST01/signal", json={"name": "sn", "value": "A"}).status_code == 202
    assert client.post("/api/users", json={"username": "n1", "password": "password123"}).status_code == 403
    _login(client, "admin1")
    assert client.post("/api/users", json={"username": "n1", "password": "password123"}).status_code == 200
    assert client.delete("/api/users/n1").status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["role"] == "admin"


def test_alerts_api_flow(auth_app):
    client, auth = auth_app
    _login(client, "admin1")
    assert client.get("/api/alerts").json() == []
    # 经 app.state 拿到同一个 store,模拟运行时采集进来的一条告警
    rec, new = client.app.state.manager.alerts.create(
        "DEMO-ST01", "sop", "missing_step", "error", "漏步:贴标签", ref_id="C9:missing_step")
    assert new is True
    rows = client.get("/api/alerts", params={"status": "pending"}).json()
    assert rows[0]["id"] == rec["id"] and rows[0]["message"] == "漏步:贴标签"
    assert client.get("/api/alerts/stats").json()["pending"] == 1
    # operator 处理:处理人自动取当前登录用户
    _login(client, "op1")
    upd = client.patch(f"/api/alerts/{rec['id']}", json={"status": "false_alarm", "comment": "画面里没漏"})
    assert upd.status_code == 200 and upd.json()["handler"] == "op1" and upd.json()["status"] == "false_alarm"
    # 处理后同 ref 再来 → 新告警
    _, new2 = client.app.state.manager.alerts.create(
        "DEMO-ST01", "sop", "missing_step", "error", "漏步:贴标签", ref_id="C9:missing_step")
    assert new2 is True and client.get("/api/alerts/stats").json()["pending"] == 1
