"""真实 build_app + 真实 ONNX 夹具的固定评测 Web 入口；权限、SHA 绑定与不可变报告。"""
import io
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from test_detector_runtime_adapter import graph
from visual.auth import AuthStore
from visual.server import build_app


def prepare(manager, name="协议评测项目"):
    """8 张分组图、一半带框、一半背景；与 CLI 测试同一套冻结导出协议。"""
    store = manager.labels
    project = store.create_project(name, ["part", "other"])
    for i in range(8):
        stream = io.BytesIO()
        Image.new("RGB", (640, 640), (i * 25, 40, 60)).save(stream, format="PNG")
        a = store.add_asset(project["id"], stream.getvalue(), f"{i}.png", {"group_id": f"camera-{i}"})
        if i % 2:
            a = store.save_draft(a["id"], [{"label_id": 0, "xyxy_norm": [.25, .25, .375, .375]}], a["revision"], "tester")
        store.confirm(a["id"], a["revision"], "reviewer", background=not i % 2)
    version = store.create_version(project["id"], seed=42, val_ratio=.5, actor="reviewer")
    return project, version


@pytest.fixture
def api(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    app = build_app([], tmp_path / "data", auth=auth)
    app.state.manager.label_engines.register("fixture-eval", graph(tmp_path), ["part", "other"], "评测夹具")
    with TestClient(app) as client:
        yield client, app.state.manager
    auth.close()


def login(client, role):
    assert client.post("/api/auth/login", json={"username": role, "password": "test-password"}).status_code == 200


def run_body(manager, **changes):
    project, version = prepare(manager)
    return {"engine_id": "fixture-eval", "project_id": project["id"], "version_id": version["id"],
            "adapter": "yolo_e2e", "confidence": .25, "iou": .5} | changes


def test_options_list_models_and_frozen_versions(api):
    client, manager = api
    prepare(manager)
    login(client, "viewer")
    data = client.get("/api/evaluations/holdout/options").json()
    assert any(m["id"] == "fixture-eval" and m["available"] for m in data["models"])
    assert data["projects"] and data["projects"][0]["versions"][0]["val_count"] > 0
    assert data["timeout_s"] > 0


def test_run_creates_immutable_report_and_lists_summary(api):
    client, manager = api
    login(client, "operator")
    created = client.post("/api/evaluations/holdout", json=run_body(manager))
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["id"].startswith("HE-")
    assert record["evaluation"]["frames"] == 4
    assert record["evaluation"]["summary"]["fp"] >= 4  # 第二类没有真值
    assert record["evaluation"]["summary"]["tp"] > 0
    assert record["publication"] == "not_requested"
    # 新增字段向后兼容:AP 随报告入库,列表摘要也携带;无真值类不混入均值。
    ap = record["average_precision"]
    assert 0 < ap["ap50"] <= 1
    assert ap["ap50"] == ap["classes"]["part"]["ap50"]
    assert ap["classes"]["other"]["ap50"] is None
    assert any(s["state"] == "background" for s in record["sources"])
    listed = client.get("/api/evaluations/holdout").json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["frames"] == 4 and item["summary"] == record["evaluation"]["summary"]
    assert item["average_precision"]["ap50"] == ap["ap50"]
    assert "sources" not in item and "evaluation" not in item
    detail = client.get(f"/api/evaluations/holdout/{record['id']}").json()
    assert detail["evaluation"]["details"] == record["evaluation"]["details"]
    assert client.get("/api/evaluations/holdout/HE-missing").status_code == 404
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        manager.holdout_evaluations._db.execute(
            "UPDATE holdout_evaluations SET body='x' WHERE id=?", (record["id"],))


def test_rejects_bad_adapter_unknown_ids_and_invalid_thresholds(api):
    client, manager = api
    login(client, "operator")
    raw = client.post("/api/evaluations/holdout", json=run_body(manager, adapter="yolo_raw"))
    assert raw.status_code == 422 and "端到端" in raw.json()["detail"]
    missing_version = client.post("/api/evaluations/holdout",
                                  json=run_body(manager, version_id="LV-nothing"))
    assert missing_version.status_code == 404
    missing_engine = client.post("/api/evaluations/holdout", json=run_body(manager, engine_id="ghost"))
    assert missing_engine.status_code == 404
    extra = client.post("/api/evaluations/holdout", json=run_body(manager, surprise=1))
    assert extra.status_code == 422
    zero = client.post("/api/evaluations/holdout", json=run_body(manager, confidence=0))
    assert zero.status_code == 422


def test_single_slot_returns_429(api):
    client, manager = api
    login(client, "operator")
    slot = manager.holdout_runner._slot
    assert slot.acquire(blocking=False)
    try:
        busy = client.post("/api/evaluations/holdout", json=run_body(manager))
        assert busy.status_code == 429
    finally:
        slot.release()


def test_timeout_kills_worker_and_returns_504(api):
    client, manager = api
    login(client, "operator")
    manager.holdout_runner.timeout_s = 0.05
    slow = client.post("/api/evaluations/holdout", json=run_body(manager))
    assert slow.status_code == 504


def test_viewer_cannot_run_but_can_read(api):
    client, manager = api
    project, version = prepare(manager)
    body = {"engine_id": "fixture-eval", "project_id": project["id"], "version_id": version["id"],
            "adapter": "yolo_e2e", "confidence": .25, "iou": .5}
    assert client.post("/api/evaluations/holdout", json=body).status_code == 401  # 未登录
    login(client, "viewer")
    assert client.post("/api/evaluations/holdout", json=body).status_code == 403
    assert client.get("/api/evaluations/holdout").status_code == 200
    assert client.get("/api/evaluations/holdout/options").status_code == 200
    assert client.get("/api/health").status_code == 200
