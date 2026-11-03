from __future__ import annotations

import io
import json
import time

import numpy as np
import pytest
from PIL import Image

from visual.label_jobs import LabelEngines, LabelJobs
from visual.labels import LabelError, LabelStore
from visual.onnx_detector import postprocess_detections


def image():
    buf = io.BytesIO(); Image.new("RGB", (100, 80), "gray").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def setup(tmp_path):
    labels = LabelStore(tmp_path / "labels")
    project = labels.create_project("标注", ["person"])
    a = labels.add_asset(project["id"], image(), "test.png")
    model = tmp_path / "test.onnx"; model.write_bytes(b"test-model")
    engines = LabelEngines(); engines.register("test", model, ["person"])
    yield labels, project, a, engines
    labels.close()


def submit(jobs, p, a, key="test"):
    return jobs.submit(p["id"], [a["id"]], "test", .4, key, "operator")


def wait(jobs, jid):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        r = jobs.get(jid)
        if r["status"] not in ("running", "queued"):
            return r
        time.sleep(.02)
    pytest.fail("worker did not finish")


def test_persist_resume_no_targets_never_confirms_background(setup, tmp_path):
    labels, p, a, engines = setup
    path = tmp_path / "jobs.db"
    jobs = LabelJobs(path, labels, engines, predictor=lambda _: [])
    j = submit(jobs, p, a)
    assert submit(jobs, p, a)["id"] == j["id"]
    jobs.close()
    jobs = LabelJobs(path, labels, engines, predictor=lambda _: [])
    jobs.start()
    out = wait(jobs, j["id"])
    assert out["no_targets"] == 1 and out["processed"] == 1
    assert labels.get_asset(a["id"])["state"] == "suggested"
    assert "_engine" not in out and "model_path" not in json.dumps(out)
    jobs.close()


def test_idempotency_conflict_and_wrong_project(setup, tmp_path):
    labels, p, a, engines = setup
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines)
    submit(jobs, p, a)
    with pytest.raises(LabelError) as e:
        jobs.submit(p["id"], [a["id"]], "test", .5, "test", "op")
    assert e.value.status == 409
    other = labels.create_project("另一个", ["person"])
    with pytest.raises(LabelError):
        submit(jobs, other, a)
    assert labels.get_asset(a["id"])["revision"] == 0
    jobs.close()


def test_confirmed_and_concurrently_changed_images_are_skipped(setup, tmp_path):
    labels, p, a, engines = setup
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines, predictor=lambda _: pytest.fail("must not infer"))
    j = submit(jobs, p, a)
    labels.confirm(a["id"], 0, "human", background=True)
    jobs.start()
    assert wait(jobs, j["id"])["skipped"] == 1
    assert labels.get_asset(a["id"])["state"] == "background"
    jobs.close()


def test_cancel_is_persisted_and_preserves_assets(setup, tmp_path):
    labels, p, a, engines = setup
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines)
    j = submit(jobs, p, a)
    assert jobs.cancel(j["id"])["status"] == "canceled"
    jobs.start(); time.sleep(.05)
    assert jobs.get(j["id"])["status"] == "canceled" and labels.get_asset(a["id"])["revision"] == 0
    jobs.close()


def test_crash_after_draft_reconciles_without_second_revision(setup, tmp_path):
    labels, p, a, engines = setup
    path = tmp_path / "jobs.db"
    jobs = LabelJobs(path, labels, engines, predictor=lambda _: pytest.fail("already inferred"))
    j = submit(jobs, p, a)
    labels.save_draft(a["id"], [], 0, "op", producer="model", provenance={"job_id": j["id"]})
    with jobs._lock:
        raw = jobs._load(j["id"]); raw["status"] = "running"; raw["items"][0]["status"] = "running"; jobs._save(raw)
    jobs.close()
    jobs = LabelJobs(path, labels, engines, predictor=lambda _: pytest.fail("already inferred"))
    jobs.start()
    assert wait(jobs, j["id"])["no_targets"] == 1
    assert labels.get_asset(a["id"])["revision"] == 1
    jobs.close()


def test_nms_uses_xywh_and_preserves_overlapping_different_classes():
    pred = np.zeros((6, 3), np.float32)
    pred[:4] = np.array([[400, 450, 400], [400, 400, 400], [20, 20, 20], [20, 20, 20]])
    pred[4, :2] = .9; pred[5, 2] = .8
    out = postprocess_detections(pred, .4, ["a", "b"], (640, 640), 1, (0, 0))
    assert len(out) == 3 and sorted(name for name, _, _ in out) == ["a", "a", "b"]


def test_transposed_layout_invalid_geometry_and_nonfinite_predictions():
    pred = np.zeros((3, 6), np.float32)
    pred[:, :4] = [[100, 100, 20, 20], [100, 100, -20, 20], [float("nan"), 100, 20, 20]]
    pred[:, 4] = .9
    out = postprocess_detections(pred, .4, ["a", "b"], (640, 640), 1, (0, 0))
    assert len(out) == 1
    with pytest.raises(ValueError):
        postprocess_detections(np.zeros((9, 20)), .4, ["a", "b"], (640, 640), 1, (0, 0))


def test_real_cpu_subprocess_prediction(tmp_path):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    pred = np.array([[320], [320], [100], [100], [.9]], dtype=np.float32)
    tensor = onnx.numpy_helper.from_array(pred)
    node = onnx.helper.make_node("Constant", [], ["out"], value=tensor)
    inp = onnx.helper.make_tensor_value_info("images", onnx.TensorProto.FLOAT, [1, 3, 640, 640])
    out = onnx.helper.make_tensor_value_info("out", onnx.TensorProto.FLOAT, [5, 1])
    model = onnx.helper.make_model(onnx.helper.make_graph([node], "label-test", [inp], [out]),
                                   opset_imports=[onnx.helper.make_opsetid("", 13)])
    model.ir_version = 9
    path = tmp_path / "test.onnx"; path.write_bytes(model.SerializeToString())
    labels = LabelStore(tmp_path / "labels"); p = labels.create_project("Test", ["person"])
    a = labels.add_asset(p["id"], image(), "中文.png")
    engines = LabelEngines(); engines.register("test", path, ["person"])
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines)
    j = submit(jobs, p, a); jobs.start()
    end = time.monotonic() + 40
    while time.monotonic() < end and jobs.get(j["id"])["status"] in ("running", "queued"):
        time.sleep(.1)
    result = jobs.get(j["id"])
    assert result["suggested"] == 1, result
    target = labels.get_asset(a["id"])["targets"][0]
    assert target["label_id"] == 0 and .89 < target["confidence"] < .91
    jobs.close(); labels.close()

def test_worker_only_exists_while_queue_has_work(setup, tmp_path):
    labels, p, a, engines = setup
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines, predictor=lambda _: [])
    try:
        jobs.start()
        assert jobs._thread is None
        for key in ("first", "after-idle"):
            j = submit(jobs, p, a, key)
            assert wait(jobs, j["id"])["status"] == "completed"
            end = time.monotonic() + 2
            while jobs._thread is not None and time.monotonic() < end:
                time.sleep(.01)
            assert jobs._thread is None
    finally:
        jobs.close()
