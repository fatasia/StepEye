"""训练HTTP权限及后台队列真实接线；不启动训练器、不加载fixture权重。"""
import io
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from visual.auth import AuthStore
from visual.labels import LabelError
from visual.server import build_app
from visual.training_jobs import TrainingJobs


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(TrainingJobs, "from_env", classmethod(
        lambda cls, root, labels, engines: cls(root, labels, engines, python=sys.executable)))
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    app = build_app([], tmp_path / "data", auth=auth)
    manager = app.state.manager
    project = manager.labels.create_project("training-api", ["part"])
    for color in ("red", "blue"):
        data = io.BytesIO(); Image.new("RGB", (32, 32), color).save(data, "PNG")
        asset = manager.labels.add_asset(project["id"], data.getvalue(), color + ".png", {"group_id": color})
        draft = manager.labels.save_draft(asset["id"], [{"label_id": 0, "xyxy_norm": [.1, .1, .7, .7]}], 0, "test")
        manager.labels.confirm(asset["id"], draft["revision"], "test")
    base = tmp_path / "base.onnx"; base.write_bytes(b"inert fixture")
    base.with_suffix(".pt").write_bytes(b"inert fixture; must not be deserialized")
    manager.label_engines.register("test-base", base, ["part"])
    def no_training(job):
        raise LabelError("controlled test stop; trainer not invoked")
    monkeypatch.setattr(manager.training, "_execute", no_training)
    with TestClient(app) as client:
        yield client, manager, auth, project["id"]
    auth.close()


def login(client, role):
    assert client.post("/api/auth/login", json={"username": role, "password": "test-password"}).status_code == 200


def payload(idem="one"):
    return {"engine_id": "test-base", "idempotency_key": idem}


def test_anonymous_cannot_read_submit_or_cancel(api):
    client, manager, auth, pid = api
    for method, url, body in [
        ("get", "/api/training/capabilities", None),
        ("get", f"/api/label-projects/{pid}/training", None),
        ("post", f"/api/label-projects/{pid}/training", payload()),
        ("post", "/api/training/jobs/TJ-fake/cancel", None),
    ]:
        assert client.request(method, url, json=body).status_code == 401
    assert manager.training.list(pid) == []


def test_viewer_read_only_and_operator_submits_with_session_actor(api):
    client, manager, auth, pid = api
    path = f"/api/label-projects/{pid}/training"
    login(client, "viewer")
    assert client.get("/api/training/capabilities").status_code == 200
    assert client.get(path).json() == []
    assert client.post(path, json=payload()).status_code == 403
    assert client.post("/api/training/jobs/TJ-fake/cancel").status_code == 403
    login(client, "operator")
    response = client.post(path, json=payload())
    assert response.status_code == 202
    job = response.json()
    assert job["actor"] == "operator"
    assert "_base" not in job and "_model" not in job
    assert client.post(path, json=payload()).json()["id"] == job["id"]
    detail = client.get(f"/api/training/jobs/{job['id']}")
    assert detail.status_code == 200 and detail.json()["project_id"] == pid
    assert client.get(path).json()[0]["id"] == job["id"]


@pytest.mark.parametrize("extra", [{"command": "arbitrary"}, {"base_model": "../outside.pt"},
                                   {"dataset_yaml": "https://example.com/data.yaml"}, {"actor": "admin"},
                                   {"epochs": 300}, {"device": "0"}])
def test_http_cannot_supply_commands_paths_actor_or_training_options(api, extra):
    client, manager, auth, pid = api
    login(client, "operator")
    response = client.post(f"/api/label-projects/{pid}/training", json=payload() | extra)
    assert response.status_code == 422
    assert manager.training.list(pid) == []


def test_mcp_read_and_draft_token_cannot_access_training_or_cancel(api):
    client, manager, auth, pid = api
    token = auth.issue_mcp_token("admin", "training-scope-test", ["read", "draft"], [pid])
    headers = {"Authorization": "Bearer " + token["token"]}
    assert client.get("/api/training/capabilities", headers=headers).status_code == 403
    assert client.get(f"/api/label-projects/{pid}/training", headers=headers).status_code == 403
    assert client.post(f"/api/label-projects/{pid}/training", json=payload(), headers=headers).status_code == 403
    assert client.post("/api/training/jobs/TJ-fake/cancel", headers=headers).status_code == 403
    assert manager.training.list(pid) == []


def test_cancel_route_is_persistent_and_late_result_cannot_win(api, monkeypatch):
    client, manager, auth, pid = api
    entered, release = threading.Event(), threading.Event()
    def execute(job):
        entered.set(); release.wait(3)
        return {"model_sha256": "test fixture, not trained", "_model": "missing"}
    monkeypatch.setattr(manager.training, "_execute", execute)
    login(client, "operator")
    try:
        response = client.post(f"/api/label-projects/{pid}/training", json=payload("cancel"))
        assert response.status_code == 202 and entered.wait(2)
        jid = response.json()["id"]
        assert client.post(f"/api/training/jobs/{jid}/cancel").json()["status"] == "cancelled"
        release.set()
        thread = manager.training._thread
        if thread: thread.join(3)
        assert client.get(f"/api/training/jobs/{jid}").json()["status"] == "cancelled"
        assert client.post(f"/api/training/jobs/{jid}/cancel").json()["status"] == "cancelled"
        assert not any(e["id"].startswith("trained-") for e in manager.label_engines.list())
    finally:
        release.set()


def test_missing_ids_and_unknown_model_return_not_found_without_queue(api):
    client, manager, auth, pid = api
    login(client, "admin")
    assert client.get("/api/training/jobs/TJ-missing").status_code == 404
    assert client.post("/api/training/jobs/TJ-missing/cancel").status_code == 404
    assert client.get("/api/label-projects/missing/training").status_code == 404
    response = client.post(f"/api/label-projects/{pid}/training", json=payload() | {"engine_id": "../../outside.pt"})
    assert response.status_code == 404
    assert manager.training.list(pid) == []
