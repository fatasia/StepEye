"""模型发布账本:真实 build_app + TestClient;权限、报告绑定、状态机与不可变账本。

评测报告直接用 manager.holdout_evaluations.create 造合法 HE 记录(与
holdout_routes.run 入库记录同构),不跑真实推理;账本/路由用真实实现。
"""
import sqlite3
import time
import uuid

from fastapi.testclient import TestClient
import pytest
from starlette.routing import Mount

from visual.auth import AuthStore
from visual.model_release import ModelReleases
from visual.model_release_routes import router as model_release_router
from visual.server import build_app

MODEL_SHA = "a" * 64


def holdout_report(manager, **changes):
    """合法 HE 记录:字段与 holdout_routes.run 的入库记录同构。"""
    record = {"schema_version": 1, "kind": "detection_validation", "model_sha256": MODEL_SHA,
              "dataset_sha256": "b" * 64, "manifest_sha256": "c" * 64, "dataset_version_id": "LV-x",
              "adapter": "yolo_e2e", "split": "val",
              "scope": "exported group/hash-isolated validation; physical workpiece independence not verified",
              "model_training_overlap": "unknown; validation split is not an independent unseen holdout claim",
              "publication": "not_requested", "excluded": [], "sources": [], "frames": [],
              "evaluation": {"frames": 4, "summary": {"tp": 1, "fp": 4, "fn": 0},
                             "classes": {"part": {"tp": 1}, "other": {"fp": 4}},
                             "false_positive_frames": 1, "missed_frames": 0, "details": []},
              "elapsed_ms": 1.0, "id": "HE-" + uuid.uuid4().hex, "created_at": time.time(),
              "actor": "operator", "engine_id": "fixture-eval", "engine_name": "评测夹具",
              "project_id": "p1", "version_id": "LV-x"}
    record.update(changes)
    manager.holdout_evaluations.create(record)
    return record


def candidate_body(record, **changes):
    return {"engine_id": record["engine_id"], "model_sha256": record["model_sha256"],
            "evaluation_id": record["id"], "adapter": record["adapter"],
            "classes": ["part", "other"]} | changes


def make_candidate(client, manager, **changes):
    record = holdout_report(manager)
    created = client.post("/api/model-releases", json=candidate_body(record, **changes))
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture
def api(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    app = build_app([], tmp_path / "data", auth=auth)
    releases = ModelReleases(tmp_path / "data" / "model_releases.db")
    api_router = model_release_router(releases, app.state.manager.holdout_evaluations)
    app.include_router(api_router)
    # build_app 以 StaticFiles("/") 收尾;后注册的路由会被静态挂载兜底吞掉(404/405),
    # 必须插到 mount 之前——与 server.py 正式接线(include_router 位于 mount 之前)一致。
    routes = app.router.routes
    added = len(api_router.routes)
    new_routes = routes[len(routes) - added:]
    del routes[len(routes) - added:]
    mount_index = next(i for i, r in enumerate(routes)
                       if isinstance(r, Mount) and r.name == "web")
    routes[mount_index:mount_index] = new_routes
    with TestClient(app) as client:
        yield client, app.state.manager, releases
    releases.close()
    auth.close()


def login(client, role):
    assert client.post("/api/auth/login", json={"username": role, "password": "test-password"}).status_code == 200


def test_create_requires_operator_and_binds_report(api):
    client, manager, releases = api
    record = holdout_report(manager)
    body = candidate_body(record)
    assert client.post("/api/model-releases", json=body).status_code == 401  # 未登录
    login(client, "viewer")
    assert client.post("/api/model-releases", json=body).status_code == 403
    login(client, "operator")
    missing = client.post("/api/model-releases", json=candidate_body(record, evaluation_id="HE-nothing"))
    assert missing.status_code == 404
    wrong_sha = client.post("/api/model-releases", json=candidate_body(record, model_sha256="f" * 64))
    assert wrong_sha.status_code == 400 and "model_sha256" in wrong_sha.json()["detail"]
    bad_hex = client.post("/api/model-releases", json=candidate_body(record, model_sha256="g" * 64))
    assert bad_hex.status_code == 400 and "十六进制" in bad_hex.json()["detail"]
    published = holdout_report(manager, publication="requested")
    rejected = client.post("/api/model-releases", json=candidate_body(published))
    assert rejected.status_code == 400 and "独立动作" in rejected.json()["detail"]
    wrong_classes = client.post("/api/model-releases", json=candidate_body(record, classes=["part"]))
    assert wrong_classes.status_code == 400 and "类别表" in wrong_classes.json()["detail"]
    wrong_engine = client.post("/api/model-releases", json=candidate_body(record, engine_id="ghost-engine"))
    assert wrong_engine.status_code == 400 and "engine_id" in wrong_engine.json()["detail"]
    wrong_adapter = client.post("/api/model-releases", json=candidate_body(record, adapter="yolo_raw"))
    assert wrong_adapter.status_code == 400 and "adapter" in wrong_adapter.json()["detail"]
    created = client.post("/api/model-releases", json=body)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["id"].startswith("MR-") and data["status"] == "candidate"
    assert data["evaluation_id"] == record["id"] and data["created_by"] == "operator"
    assert releases.get(data["id"])["model_sha256"] == MODEL_SHA


def test_state_machine_approve_activate_and_rollback_restores_previous(api):
    client, manager, releases = api
    login(client, "operator")
    first = make_candidate(client, manager)
    assert client.post(f"/api/model-releases/{first['id']}/approve").status_code == 403  # 审批仅 admin
    assert client.post(f"/api/model-releases/{first['id']}/activate",
                       json={"scene": "station-a"}).status_code == 403
    login(client, "admin")
    early = client.post(f"/api/model-releases/{first['id']}/activate", json={"scene": "station-a"})
    assert early.status_code == 400 and "approved" in early.json()["detail"]  # 未审批先激活
    approved = client.post(f"/api/model-releases/{first['id']}/approve").json()
    assert approved["status"] == "approved" and approved["approved_by"] == "admin"
    assert client.post(f"/api/model-releases/{first['id']}/approve").status_code == 400  # 重复审批
    activated = client.post(f"/api/model-releases/{first['id']}/activate",
                            json={"scene": "station-a"}).json()
    assert activated["status"] == "active" and activated["replaced_release_id"] is None
    repeat = client.post(f"/api/model-releases/{first['id']}/activate", json={"scene": "station-b"})
    assert repeat.status_code == 400 and "approved" in repeat.json()["detail"]  # 重复 active 拒绝
    second = make_candidate(client, manager)
    client.post(f"/api/model-releases/{second['id']}/approve")
    promoted = client.post(f"/api/model-releases/{second['id']}/activate",
                           json={"scene": "station-a"}).json()
    assert promoted["replaced_release_id"] == first["id"]
    assert client.get(f"/api/model-releases/{first['id']}").json()["status"] == "superseded"
    actives = [i for i in client.get("/api/model-releases").json()["items"]
               if i["status"] == "active" and i["scene"] == "station-a"]
    assert [i["id"] for i in actives] == [second["id"]]  # 同 scene 唯一 active
    rolled = client.post(f"/api/model-releases/{second['id']}/rollback").json()
    assert rolled["status"] == "rolled_back"
    restored = client.get(f"/api/model-releases/{first['id']}").json()
    assert restored["status"] == "active" and restored["replaced_release_id"] == second["id"]
    assert client.post(f"/api/model-releases/{first['id']}/rollback").status_code == 200  # 对称回滚
    assert client.get(f"/api/model-releases/{second['id']}").json()["status"] == "active"
    third = make_candidate(client, manager)
    client.post(f"/api/model-releases/{third['id']}/approve")
    client.post(f"/api/model-releases/{third['id']}/activate", json={"scene": "station-b"})
    empty = client.post(f"/api/model-releases/{third['id']}/rollback")
    assert empty.status_code == 400 and "没有更早" in empty.json()["detail"]  # 无上一版
    fresh = make_candidate(client, manager)
    assert client.post(f"/api/model-releases/{fresh['id']}/rollback").status_code == 400


def test_list_detail_viewer_access_and_transitions(api):
    client, manager, releases = api
    login(client, "operator")
    first = make_candidate(client, manager)
    login(client, "viewer")
    listed = client.get("/api/model-releases").json()
    assert listed["total"] == 1 and listed["items"][0]["evaluation_id"] == first["evaluation_id"]
    assert client.get("/api/model-releases?limit=0").status_code == 400
    assert client.get("/api/model-releases?offset=-1").status_code == 400
    detail = client.get(f"/api/model-releases/{first['id']}").json()
    assert detail["transitions"] == [{"revision": 1, "status": "candidate", "action": "candidate",
                                      "actor": "operator", "at": detail["created_at"]}]
    assert client.get("/api/model-releases/MR-missing").status_code == 404


def test_ledger_is_immutable(api):
    client, manager, releases = api
    login(client, "operator")
    first = make_candidate(client, manager)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        releases._db.execute("UPDATE model_releases SET body='x' WHERE release_id=?", (first["id"],))
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        releases._db.execute("DELETE FROM model_releases WHERE release_id=?", (first["id"],))
