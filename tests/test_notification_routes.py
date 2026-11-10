"""Notification wiring must preserve alerts and keep channel details admin-only."""
from fastapi.testclient import TestClient
from visual.auth import AuthStore
from visual.server import build_app


class Dispatcher:
    def __init__(self, configured=True):
        self.configured = configured
        self.submitted = []
        self.closed = False

    def snapshot(self):
        return {"configured": self.configured, "enabled": self.configured,
                "worker": "idle", "channels": []}

    def submit(self, record):
        self.submitted.append(record)
        return {"accepted": True, "reason": "queued", "channels": []}

    def close(self):
        self.closed = True


def test_dispatch_wiring_and_admin_status(tmp_path, monkeypatch):
    dispatcher = Dispatcher()
    monkeypatch.setattr("visual.notifications.NotificationDispatcher.from_env", lambda *a, **kw: dispatcher)
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    app = build_app([], tmp_path / "data", auth=auth)
    try:
        with TestClient(app) as client:
            assert client.get("/api/notifications/status").status_code == 401
            for role in ("viewer", "operator", "admin"):
                client.post("/api/auth/login", json={"username": role, "password": "test-password"})
                assert client.get("/api/notifications/status").status_code == (200 if role == "admin" else 403)
            manager = app.state.manager
            rec, _ = manager.alerts.create("station", "sop", "timeout", "warn", "timeout")
            manager._push_out(rec)
            assert dispatcher.submitted == [rec]
            assert manager.alerts.get(rec["id"]) is not None
        assert dispatcher.closed
    finally:
        auth.close()
