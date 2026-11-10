import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from visual.mechanism_motor import MotorBody, MotorService, FEATURE_HELPER_SHA, aggregate
from visual.mechanism_motor_routes import motor_router
from visual.mechanism_routes import MechanismReports


def body():
    return {"title": "motor", "dataset": "MCC5-THU-Motor", "feature_helper_sha256": FEATURE_HELPER_SHA,
            "feature_names": [f"feature-{i}" for i in range(114)],
            "records": [{"id": "record-1", "complete_record": True, "features": [[1.] * 114]}]}


def test_record_mean_and_missing_windows():
    rows = aggregate(np.array([[.2, .8], [.4, .6], [.9, .1]], np.float32), [2, 0, 1],
                     ["a", "empty", "b"], ["health", "fault"])
    assert rows[0]["label"] == "fault" and rows[0]["active_windows"] == 2
    assert rows[0]["probabilities"] == pytest.approx([.3, .7])
    assert rows[1]["status"] == "unsupported" and rows[1]["label"] is None
    assert rows[2]["label"] == "health"


@pytest.mark.parametrize("change", ["shape", "nan", "definition", "duplicate", "incomplete", "overflow"])
def test_input_protocol(change):
    value = body()
    if change == "shape": value["records"][0]["features"][0].pop()
    if change == "nan": value["records"][0]["features"][0][0] = float("nan")
    if change == "definition": value["feature_helper_sha256"] = "a" * 64
    if change == "duplicate": value["records"].append(value["records"][0])
    if change == "incomplete": value["records"][0]["complete_record"] = False
    if change == "overflow": value["records"][0]["features"] *= 65
    with pytest.raises(ValidationError):
        MotorBody.model_validate(value)


def test_missing_asset_is_unavailable(tmp_path):
    assert MotorService(tmp_path).capabilities()["available"] is False


def test_single_slot_and_failure_release(tmp_path):
    service = MotorService(tmp_path)
    service._slot.acquire()
    try:
        with pytest.raises(RuntimeError, match="忙"):
            service.analyze(body())
    finally:
        service._slot.release()
    with pytest.raises(ValueError, match="未安装"):
        service.analyze(body())
    assert service._slot.acquire(False)
    service._slot.release()


def test_analyzer_contract_and_shared_report_api(tmp_path, monkeypatch):
    import onnxruntime
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(json.dumps({"feature_names": body()["feature_names"],
        "classes": ["health", "fault"], "probability_output": "probabilities", "protocol": "held-out-torque-profile"}))
    monkeypatch.setattr("visual.mechanism_motor.pinned_bundle", lambda root: bundle)
    monkeypatch.setattr("visual.model_signature.verify_model_policy", lambda path: {"verified": True})

    class Session:
        def __init__(self, *args, **kwargs):
            assert kwargs["providers"] == ["CPUExecutionProvider"]
        def run(self, outputs, inputs):
            assert inputs["features"].shape[1] == 114
            return [np.array([[.3, .7]], dtype=np.float32)]

    monkeypatch.setattr(onnxruntime, "InferenceSession", Session)
    service = MotorService(tmp_path)
    wrong = body(); wrong["feature_names"].reverse()
    with pytest.raises(ValueError, match="顺序"):
        service.analyze(wrong)
    store = MechanismReports(tmp_path / "reports.db")
    app = FastAPI(); app.include_router(motor_router(store, service))
    try:
        with TestClient(app) as client:
            response = client.post("/api/mechanism/motor/analyses", json=body())
            assert response.status_code == 201, response.text
            saved = response.json()
            assert saved["kind"] == "motor_rf_shadow"
            assert saved["result"]["records"][0]["label"] == "fault"
            assert saved["result"]["signature"]["verified"]
            assert saved["result"]["production_approval"] is False
            assert store.get(saved["id"]) == saved
            assert store.list()["items"][0]["status"] == "shadow_evidence"
            empty = body(); empty["records"][0]["features"] = []
            assert client.post("/api/mechanism/motor/analyses", json=empty).json()["result"]["status"] == "unsupported"
    finally:
        store.close()
