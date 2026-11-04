"""模型发布账本:真实 build_app + TestClient;权限、报告绑定、状态机与不可变账本。

评测报告直接用 manager.holdout_evaluations.create 造合法 HE 记录(与
holdout_routes.run 入库记录同构),不跑真实推理;账本/路由用真实实现。
"""
import sqlite3
import time
import uuid

from fastapi.testclient import TestClient
import pytest

from visual.auth import AuthStore
from visual.model_release import ModelReleases
from visual.model_release_routes import router as model_release_router
from visual.server import build_app

import hashlib
MODEL_SHA = hashlib.sha256(b"dummy").hexdigest()


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
    # candidate 创建会快照引擎的模型文件路径:登记一个哑文件即可
    dummy = tmp_path / "fixture.onnx"
    dummy.write_bytes(b"dummy")
    app.state.manager.label_engines.register("fixture-eval", dummy, ["part", "other"], "发布夹具")
    releases = ModelReleases(tmp_path / "data" / "model_releases.db")
    # build_app 的最后一个动作就是 StaticFiles("/") 挂载:按位置取,不做任何类型/名称假设
    routes = app.router.routes
    mount_index = len(routes) - 1
    assert hasattr(routes[-1], "app"), "静态挂载缺失,路由无法生效"
    api_router = model_release_router(releases, app.state.manager.holdout_evaluations)
    app.include_router(api_router)  # 追加到挂载之后,挪到挂载之前才能被路由到
    new_routes = routes[mount_index + 1:]
    del routes[mount_index + 1:]
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


def test_activation_applies_to_runtime_sha_mismatch_skips(tmp_path):
    """生效路径:激活后工位重载即覆盖检测器;引擎 SHA 漂移时跳过覆盖并保持 YAML 原样。"""
    import yaml
    from pathlib import Path
    import numpy as np
    import onnx
    from visual.label_jobs import fingerprint

    def variant_graph(folder, tag):
        """带差异字节的常量 e2e 检测图;tag 写进模型属性使 SHA 可区分。"""
        folder.mkdir(parents=True, exist_ok=True)
        pred = np.zeros((1, 300, 6), np.float32)
        pred[0, 0] = [160, 160, 240, 240, .9, 0]
        pred[0, 1] = [300, 300, 400, 400, .8, 1]
        node = onnx.helper.make_node('Constant', [], ['output'], value=onnx.numpy_helper.from_array(pred))
        g = onnx.helper.make_graph([node], 'fixture-' + tag,
                                   [onnx.helper.make_tensor_value_info('images', onnx.TensorProto.FLOAT, [1, 3, 640, 640])],
                                   [onnx.helper.make_tensor_value_info('output', onnx.TensorProto.FLOAT, list(pred.shape))])
        m = onnx.helper.make_model(g, opset_imports=[onnx.helper.make_opsetid('', 13)])
        m.ir_version = 8
        onnx.helper.set_model_props(m, {'names': "{0: 'part', 1: 'other'}", 'end2end': 'true',
                                        'task': 'detect', 'variant': tag})
        p = folder / 'fixture.onnx'
        onnx.save(m, p)
        return p

    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator"):
        auth.set_user(role, "test-password", role)
    station = tmp_path / "station.yaml"
    station.write_text(yaml.safe_dump({
        "station": "ST-R", "name": "发布生效工位",
        "source": {"type": "sim", "width": 640, "height": 480},
        "hands": {"enabled": False},
        "zones": [{"id": "post", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        "safety": {"enabled": True, "rules": [{"id": "idle", "kind": "person_absent", "zone": "post"}]},
    }), encoding="utf-8")
    app = build_app([station], tmp_path / "data", auth=auth)
    manager = app.state.manager
    first_model = variant_graph(tmp_path / "m1", "r1")
    second_model = variant_graph(tmp_path / "m2", "r2")
    releases = manager.model_releases
    try:
        with TestClient(app) as client:
            login(client, "admin")

            def full_cycle(model, conf):
                # 新版本模型文件上线 → 引擎登记指向新文件(对应真实发布流程)
                manager.label_engines.register("fixture-eval", model, ["part", "other"], "发布夹具")
                record = holdout_report(manager, model_sha256=fingerprint(model),
                                        engine_id="fixture-eval", adapter="yolo_e2e")
                created = client.post("/api/model-releases",
                                      json=candidate_body(record, model_sha256=fingerprint(model)))
                assert created.status_code == 201, created.text
                rid = created.json()["id"]
                assert client.post(f"/api/model-releases/{rid}/approve").status_code == 200
                activated = client.post(f"/api/model-releases/{rid}/activate", json={"scene": "ST-R"})
                assert activated.status_code == 200, activated.text
                return activated.json()

            first = full_cycle(first_model, .25)
            assert first["applied_to_runtime"] is True and first["pending_reload"] is False
            detector = manager.runtimes["ST-R"].cfg.detector
            assert detector.enabled and detector.adapter == "yolo_e2e"
            assert Path(detector.model) == Path(first_model)
            assert detector.class_names == ["part", "other"]

            second = full_cycle(second_model, .3)
            assert second["applied_to_runtime"] is True
            assert Path(manager.runtimes["ST-R"].cfg.detector.model) == Path(second_model)

            rolled = client.post(f"/api/model-releases/{second['id']}/rollback")
            assert rolled.status_code == 200 and rolled.json()["applied_to_runtime"] is True
            assert Path(manager.runtimes["ST-R"].cfg.detector.model) == Path(first_model)

            # 引擎文件漂移(账本记录的模型文件被替换):重载跳过覆盖,YAML 原样生效
            first_model.write_bytes(first_model.read_bytes()[:-1] + b"\x00")
            manager.reload("ST-R")
            skipped = manager.runtimes["ST-R"].cfg.detector
            assert not skipped.enabled and skipped.adapter == "yolo_raw"
    finally:
        releases.close()
        auth.close()
