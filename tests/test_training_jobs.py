"""Training orchestration checks use controlled runners, never a fabricated trained model."""
import io
import json
import sys
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image
import pytest

from visual.labels import LabelStore, LabelError
from visual.label_jobs import LabelEngines, fingerprint
from visual.training_jobs import TrainingJobs


@pytest.fixture
def assets(tmp_path):
    labels = LabelStore(tmp_path / "labels")
    p = labels.create_project("parts", ["part"])
    for color in ("red", "blue"):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color).save(buf, "PNG")
        a = labels.add_asset(p["id"], buf.getvalue(), color + ".png", {"group_id": color})
        draft = labels.save_draft(a["id"], [{"label_id":0,"xyxy_norm":[.1,.1,.5,.5]}], 0, "test")
        labels.confirm(a["id"], draft["revision"], "test")
    model = tmp_path / "base.onnx"
    model.write_bytes(b"fixture")
    model.with_suffix(".pt").write_bytes(b"fixture checkpoint, not executable")
    engines = LabelEngines()
    engines.register("base", model, ["part"])
    yield labels, p["id"], engines
    labels.close()


def wait(jobs, jid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        out = jobs.get(jid)
        if out["status"] not in ("queued", "running"):
            return out
        time.sleep(.01)
    pytest.fail("training worker did not retire")


def test_unconfigured_zero_worker(assets, tmp_path):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines)
    try:
        assert jobs._thread is None
        with pytest.raises(LabelError, match="隔离训练"):
            jobs.submit(pid, "base", "one", "test")
        assert jobs.list(pid) == []
    finally:
        jobs.close()


def test_idempotency_failure_and_idle_retirement(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    def fail(job):
        raise RuntimeError("secret/path must never be returned")
    monkeypatch.setattr(jobs, "_execute", fail)
    try:
        job = jobs.submit(pid, "base", "one", "test")
        out = wait(jobs, job["id"])
        assert out["status"] == "failed"
        assert "secret" not in json.dumps(out)
        assert "_base" not in out
        assert jobs.submit(pid, "base", "one", "test")["id"] == job["id"]
        deadline = time.monotonic() + 2
        while jobs._thread and time.monotonic() < deadline:
            time.sleep(.01)
        assert jobs._thread is None
    finally:
        jobs.close()


def test_cancel_cannot_be_overwritten_by_late_result(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    entered, release = threading.Event(), threading.Event()
    def run(job):
        entered.set()
        assert release.wait(3)
        return {"model_sha256":"fixture", "_model":"missing"}
    monkeypatch.setattr(jobs, "_execute", run)
    try:
        job = jobs.submit(pid, "base", "one", "test")
        assert entered.wait(2)
        assert jobs.cancel(job["id"])["status"] == "cancelled"
        release.set()
        jobs._thread.join(3)
        assert jobs.get(job["id"])["status"] == "cancelled"
    finally:
        release.set()
        jobs.close()


def test_recover_interrupted_without_retraining(assets, tmp_path):
    labels, pid, engines = assets
    root = tmp_path / "training"
    jobs = TrainingJobs(root, labels, engines, python=sys.executable)
    job = {"id":"TJ-test", "project_id":pid, "idempotency_key":"one", "status":"running"}
    jobs._save(job)
    # Simulate metadata left by a crashed process, without running a worker.
    jobs._release_locked()
    jobs = TrainingJobs(root, labels, engines, python=sys.executable)
    try:
        assert jobs.get("TJ-test")["status"] == "interrupted"
        assert jobs._thread is None
    finally:
        jobs.close()


def test_queue_capacity_and_cancel(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    entered, release = threading.Event(), threading.Event()
    def run(job):
        entered.set(); release.wait(5)
        raise LabelError("controlled failure")
    monkeypatch.setattr(jobs, "_execute", run)
    try:
        first = jobs.submit(pid, "base", "0", "test")
        assert entered.wait(2)
        others = [jobs.submit(pid, "base", str(i), "test") for i in range(1,4)]
        with pytest.raises(LabelError) as exc:
            jobs.submit(pid, "base", "overflow", "test")
        assert exc.value.status == 429
        for job in others:
            jobs.cancel(job["id"])
        release.set()
        wait(jobs, first["id"])
    finally:
        release.set(); jobs.close()


def test_real_prepare_extracts_immutable_human_version_and_pins_spec(assets, tmp_path, monkeypatch):
    """跑真实版本创建/解包/预检；不运行训练器，不把fixture当模型成果。"""
    from visual.training_worker import inspect_training_dataset
    labels, pid, engines = assets
    pending = io.BytesIO(); Image.new("RGB", (64, 64), "green").save(pending, "PNG")
    labels.add_asset(pid, pending.getvalue(), "unreviewed.png")
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    prepared = []
    def execute(job):
        prepared.append(jobs._prepare(job))
        raise LabelError("controlled stop after real preflight")
    monkeypatch.setattr(jobs, "_execute", execute)
    try:
        job = jobs.submit(pid, "base", "prepare", "operator-test")
        assert wait(jobs, job["id"])["status"] == "failed"
        path, spec = prepared[0]
        assert path.parent.name == job["id"]
        assert spec["epochs"] == 30 and spec["batch"] == 4 and spec["imgsz"] == 640
        assert fingerprint(Path(spec["base_model"])) == job["base_sha256"]
        checked = inspect_training_dataset(spec["dataset_yaml"])
        assert checked["images"] == {"train": 1, "val": 1}
        assert checked["dataset_sha256"] == spec["dataset_sha256"]
        assert checked["dataset_version"] == job["version_id"]
        assert checked["classes"] == ["part"]
        assert not Path(spec["output_dir"]).exists()
    finally:
        jobs.close()


def test_real_prepare_rejects_checkpoint_mutation_before_any_trainer(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    def execute(job):
        Path(job["_base"]).write_bytes(b"changed fixture, not executable")
        jobs._prepare(job)
        pytest.fail("changed base passed real preflight")
    monkeypatch.setattr(jobs, "_execute", execute)
    try:
        job = jobs.submit(pid, "base", "changed-base", "test")
        out = wait(jobs, job["id"])
        assert out["status"] == "failed" and "基座模型指纹变化" in out["error"]
        assert jobs._process is None
    finally:
        jobs.close()


def test_real_prepare_checks_cancellation_between_zip_entries(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable)
    entered, release = threading.Event(), threading.Event()
    original_prepare = jobs._prepare
    def execute(job):
        entered.set(); assert release.wait(3)
        return original_prepare(job)  # 取消后真实解包第一项即拒绝。
    monkeypatch.setattr(jobs, "_execute", execute)
    try:
        job = jobs.submit(pid, "base", "cancel-prepare", "test")
        assert entered.wait(2)
        jobs.cancel(job["id"]); release.set()
        jobs._thread.join(3)
        assert jobs.get(job["id"])["status"] == "cancelled"
        assert jobs._process is None
        assert not (jobs.root / job["id"] / "spec.json").exists()
    finally:
        release.set(); jobs.close()


def test_real_child_timeout_kills_and_reaps_without_candidate(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable, timeout_s=.08)
    original, children = subprocess.Popen, []
    def spawn(args, **kwargs):
        proc = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        job = jobs.submit(pid, "base", "timeout", "test")
        out = wait(jobs, job["id"])
        assert out["status"] == "failed" and "超过时间上限" in out["error"]
        assert len(children) == 1 and children[0].poll() is not None
        assert not any(e["id"].startswith("trained-") for e in engines.list())
    finally:
        jobs.close()


def test_real_child_cancel_kills_and_never_registers_candidate(assets, tmp_path, monkeypatch):
    labels, pid, engines = assets
    jobs = TrainingJobs(tmp_path / "training", labels, engines, python=sys.executable, timeout_s=30)
    original, children = subprocess.Popen, []
    def spawn(args, **kwargs):
        proc = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        job = jobs.submit(pid, "base", "cancel-real-child", "test")
        deadline = time.monotonic() + 3
        while not children and time.monotonic() < deadline: time.sleep(.01)
        assert children
        assert jobs.cancel(job["id"])["status"] == "cancelled"
        thread = jobs._thread
        if thread: thread.join(3)
        assert children[0].poll() is not None
        assert jobs.get(job["id"])["status"] == "cancelled"
        assert not any(e["id"].startswith("trained-") for e in engines.list())
    finally:
        jobs.close()


@pytest.mark.parametrize("device", ["1", "0,1", "cuda", "-1", "cpu;echo"])
def test_environment_rejects_unsupported_device_before_opening_queue(assets, tmp_path, monkeypatch, device):
    labels, pid, engines = assets
    monkeypatch.setattr("visual.llm_provider.load_env_file", lambda path: {})
    monkeypatch.setenv("STEPEYE_TRAIN_DEVICE", device)
    root = tmp_path / "should-not-exist"
    with pytest.raises(ValueError):
        TrainingJobs.from_env(root, labels, engines)
    assert not root.exists()


@pytest.mark.parametrize("device", ["cpu", "0"])
def test_supported_device_config_does_not_start_worker(assets, tmp_path, monkeypatch, device):
    labels, pid, engines = assets
    monkeypatch.setattr("visual.llm_provider.load_env_file", lambda path: {})
    monkeypatch.setenv("STEPEYE_TRAIN_DEVICE", device)
    monkeypatch.setenv("STEPEYE_TRAIN_PYTHON", sys.executable)
    jobs = TrainingJobs.from_env(tmp_path / "training", labels, engines)
    try:
        assert jobs.capabilities()["device"] == device and jobs._thread is None
    finally:
        jobs.close()
