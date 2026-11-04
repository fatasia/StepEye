import copy

import pytest
from fastapi.testclient import TestClient

from visual.accuracy_monitor import compare_reports
from visual.auth import AuthStore
from visual.detection_eval import evaluate_boxes
from visual.server import build_app


def report(identifier, detected=30, frames=30):
    box = {"label": "part", "box": [.1, .1, .4, .4]}
    rows = [{"id": str(i), "reviewed": True, "truth": [box],
             "predictions": [dict(box, confidence=.8)] if i < detected else []} for i in range(frames)]
    return {"id": "HE-" + identifier, "kind": "detection_validation", "created_at": 1,
            "model_sha256": "a" * 64, "dataset_sha256": identifier,
            "adapter": "yolo_raw", "frames": rows, "evaluation": evaluate_boxes(["part"], rows)}


def test_observed_recall_drop_is_not_automatic_rollback():
    result = compare_reports(report("base"), report("current", 15))
    assert result["status"] == "observed_drop"
    part = result["classes"]["part"]
    assert part["baseline"]["tp"] == 30 and part["current"]["fn"] == 15
    assert part["metrics"]["recall"] == {"delta": -.5, "sufficient": True, "observed_drop": True}
    assert not part["metrics"]["precision"]["sufficient"]
    assert result["dataset_relation"] == "different_datasets"
    assert result["automatic_rollback"] is False


def test_insufficient_frames_and_class_counts():
    result = compare_reports(report("base", frames=5), report("current", 0, frames=5))
    assert result["status"] == "insufficient_data"
    assert result["classes"]["part"]["current"]["precision"] is None
    assert not result["classes"]["part"]["metrics"]["recall"]["observed_drop"]


def test_same_data_and_unchanged_rates():
    before, after = report("base"), report("current")
    after["dataset_sha256"] = before["dataset_sha256"]
    result = compare_reports(before, after)
    assert result["status"] == "no_observed_drop"
    assert result["dataset_relation"] == "same_dataset"


@pytest.mark.parametrize("key,value", [("model_sha256", "b"*64), ("adapter", "yolo_e2e"), ("kind", "classification")])
def test_incompatible_report_rejected(key, value):
    current = report("current")
    current[key] = value
    with pytest.raises(ValueError):
        compare_reports(report("base"), current)


@pytest.mark.parametrize("mutation", ["threshold", "classes", "truth", "counts"])
def test_incompatible_evaluation_rejected(mutation):
    current = report("current")
    if mutation == "threshold": current["evaluation"]["confidence_threshold"] = .4
    if mutation == "classes": current["evaluation"]["classes"]["other"] = current["evaluation"]["classes"].pop("part")
    if mutation == "truth": current["frames"][0]["reviewed"] = False
    if mutation == "counts": current["evaluation"]["classes"]["part"]["tp"] = -1
    with pytest.raises(ValueError):
        compare_reports(report("base"), current)


def test_thresholds_and_input_reports_remain_unchanged():
    before, after = report("base"), report("current", 27)
    original = copy.deepcopy((before, after))
    assert compare_reports(before, after, min_drop=.1)["status"] == "observed_drop"
    assert compare_reports(before, after, min_drop=.2)["status"] == "no_observed_drop"
    assert (before, after) == original
    with pytest.raises(ValueError): compare_reports(before, before)
    with pytest.raises(ValueError): compare_reports(before, after, min_samples=True)
    with pytest.raises(ValueError): compare_reports(before, after, min_drop=float("nan"))


def test_compare_api_permissions_missing_reports_and_immutable_sources(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    for role in ("admin", "operator", "viewer"):
        auth.set_user(role, "test-password", role)
    auth.set_user("scoped", "test-password", "operator", project_ids=[])
    app = build_app([], tmp_path / "data", auth=auth)
    store = app.state.manager.holdout_evaluations
    store.create(report("base")); store.create(report("current", 15))
    before = store.get("HE-base")
    body = {"baseline_id": "HE-base", "current_id": "HE-current"}
    try:
        with TestClient(app) as client:
            endpoint = "/api/evaluations/holdout/compare"
            assert client.post(endpoint, json=body).status_code == 401
            for user in ("viewer", "scoped"):
                client.post("/api/auth/login", json={"username": user, "password": "test-password"})
                assert client.post(endpoint, json=body).status_code == 403
            client.post("/api/auth/login", json={"username": "operator", "password": "test-password"})
            response = client.post(endpoint, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "observed_drop"
            assert client.post(endpoint, json=body | {"current_id": "HE-missing"}).status_code == 404
            assert client.post(endpoint, json=body | {"min_drop": 0}).status_code == 422
            assert client.post(endpoint, json=body | {"min_samples": True}).status_code == 422
            assert client.post(endpoint, json=body | {"model_path": "/any"}).status_code == 422
            assert store.get("HE-base") == before
            assert store.list()["total"] == 2
    finally:
        auth.close()
