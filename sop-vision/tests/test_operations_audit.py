from fastapi.testclient import TestClient
from visual.audit import AuditStore
from visual.auth import AuthStore
from visual.server import build_app


def test_audit_bounded_and_persistent(tmp_path):
    path = tmp_path / "audit.db"
    store = AuditStore(path, limit=3)
    for i in range(5):
        store.append("admin", "POST", "/api/users", 200)
    rows = store.list()["items"]
    assert len(rows) == 3
    assert rows[0]["id"] > rows[-1]["id"]
    assert len(store.list(before=rows[0]["id"])["items"]) == 2
    store.close()
    store = AuditStore(path, limit=3)
    assert len(store.list()["items"]) == 3
    store.close()


def test_audit_admin_only_without_password_or_path_parameters(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    auth.set_user("admin", "test-password", "admin")
    auth.set_user("viewer", "test-password", "viewer")
    app = build_app([], tmp_path / "data", auth=auth)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username":"admin","password":"test-password"})
        assert client.post("/api/users", json={"username":"test-user","password":"private-test-password","role":"viewer"}).status_code == 200
        assert client.delete("/api/users/test-user").status_code == 200
        response = client.get("/api/operations/logs")
        assert response.status_code == 200
        assert "private-test-password" not in response.text
        assert "test-user" not in response.text
        assert any(r["route"] == "/api/users/{username}" for r in response.json()["items"])
        client.post("/api/auth/login", json={"username":"viewer","password":"test-password"})
        assert client.get("/api/operations/logs").status_code == 403
    auth.close()
