from __future__ import annotations

import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visual.auth import AuthStore
from visual.server import build_app


@pytest.fixture
def api(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    app = build_app([], tmp_path / "data", auth=auth)
    with TestClient(app) as client:
        yield client, auth, app.state.manager
    auth.close()


def login(client, role="admin"):
    assert client.post("/api/auth/login", json={"username": role, "password": "test-password"}).status_code == 200


def project(client, name="Test"):
    return client.post("/api/label-projects", json={"name": name, "classes": ["part"]}).json()


def token(client, pid, scopes=None):
    r = client.post("/api/mcp/tokens", json={"name": "test", "project_ids": [pid], "scopes": scopes or ["read"]})
    assert r.status_code == 201, r.text
    return r.json()


def test_scoped_read_images_write_and_revocation(api):
    client, auth, manager = api
    login(client)
    p1, p2 = project(client), project(client, "Other")
    data = io.BytesIO(); Image.new("RGB", (80, 60)).save(data, "PNG")
    asset = client.post(f"/api/label-projects/{p1['id']}/assets", files={"files": ("a.png", data.getvalue(), "image/png")}).json()["items"][0]
    t = token(client, p1["id"])
    headers = {"Authorization": "Bearer " + t["token"]}
    assert [r["id"] for r in client.get("/api/label-projects", headers=headers).json()] == [p1["id"]]
    assert client.get(f"/api/label-projects/{p2['id']}/quality", headers=headers).status_code == 403
    assert client.get(f"/api/label-assets/{asset['id']}/annotations", headers=headers).status_code == 200
    assert client.get(f"/api/label-assets/{asset['id']}/image", headers=headers).status_code == 403
    assert client.post(f"/api/label-assets/{asset['id']}/confirm", json={"expected_revision": 0, "background": True}, headers=headers).status_code == 403
    assert client.get("/api/stations", headers=headers).status_code == 403
    assert client.get("/api/auth/me", headers=headers).status_code == 403
    assert client.get("/api/mcp/tokens", headers=headers).status_code == 403
    assert t["token"] not in json.dumps(auth.list_mcp_tokens()) + json.dumps(auth.mcp_audit())
    assert client.delete(f"/api/mcp/tokens/{t['id']}").status_code == 200
    assert client.get("/api/label-projects", headers=headers).status_code == 401
    t2 = token(client, p1["id"], ["read", "images"])
    assert client.get(f"/api/label-assets/{asset['id']}/image", headers={"Authorization": "Bearer " + t2["token"]}).status_code == 200


def test_role_changes_expiry_and_account_deletion_do_not_resurrect_token(api, monkeypatch):
    client, auth, manager = api
    login(client); p = project(client); t = token(client, p["id"])
    assert auth.verify_mcp_token(t["token"])
    auth.set_user("admin", "test-password", "operator")
    assert auth.verify_mcp_token(t["token"]) is None
    auth.set_user("admin", "test-password", "admin")
    with monkeypatch.context() as m:
        m.setattr("visual.auth.time.time", lambda: t["expires_at"] + 1)
        assert auth.verify_mcp_token(t["token"]) is None
    auth.delete_user("admin"); auth.set_user("admin", "test-password", "admin")
    assert auth.verify_mcp_token(t["token"]) is None


def test_viewer_cannot_create_tokens_or_write_labels_and_conflicts_are_409(api):
    client, auth, manager = api
    login(client); p = project(client)
    data = io.BytesIO(); Image.new("RGB", (80, 60)).save(data, "PNG")
    a = manager.labels.add_asset(p["id"], data.getvalue(), "a.png")
    payload = {"expected_revision": 0, "targets": [{"label_id": 0, "xyxy_norm": [.1, .1, .4, .6]}]}
    path = f"/api/label-assets/{a['id']}/annotations"
    assert client.put(path, json=payload).status_code == 200
    assert client.put(path, json=payload).status_code == 409
    login(client, "viewer")
    assert client.post("/api/mcp/tokens", json={"name": "x"}).status_code == 403
    assert client.put(path, json=payload).status_code == 403
    assert client.get(path).status_code == 200


def test_tokens_require_auth_even_when_local_tool_mode(tmp_path):
    app = build_app([], tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/mcp/tokens", json={"name": "test"}).status_code == 409
        assert client.get("/api/label-projects", headers={"Authorization": "Bearer se_mcp_bad"}).status_code == 401

def test_agent_draft_scope_never_confirms_or_crosses_projects(api):
    client, auth, manager = api
    login(client)
    p, other = project(client), project(client, "Other")
    img = io.BytesIO(); Image.new("RGB", (40, 40)).save(img, "PNG")
    a, b = [manager.labels.add_asset(x["id"], img.getvalue(), "x.png") for x in (p, other)]
    route = lambda asset: f"/api/label-assets/{asset['id']}/agent-draft"
    body = {"expected_revision": 0, "targets": [{"label_id": 0, "xyxy_norm": [.1, .1, .9, .9]}]}
    readonly = {"Authorization": "Bearer " + token(client, p["id"])["token"]}
    write = {"Authorization": "Bearer " + token(client, p["id"], ["read", "draft"])["token"]}
    assert client.put(route(a), json=body, headers=readonly).status_code == 403
    assert client.put(route(b), json=body, headers=write).status_code == 403
    result = client.put(route(a), json=body, headers=write)
    assert result.status_code == 200 and result.json()["producer"] == "agent" and result.json()["state"] == "suggested"
    assert client.put(route(a), json=body, headers=write).status_code == 409
    confirm = route(a).replace("agent-draft", "confirm")
    assert client.post(confirm, json={"expected_revision": 1}, headers=write).status_code == 403
    assert client.post(confirm, json={"expected_revision": 1}).status_code == 200
    assert client.put(route(a), json={**body, "expected_revision": 2}, headers=write).status_code == 409
