"""预标注故障验收：真实挂起子进程、取消、人工并发和有界入队。"""
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import subprocess
import sys
import threading
import time

from PIL import Image
import pytest

from visual.auth import AuthStore
from visual.label_jobs import LabelEngines, LabelJobs
from visual.labels import LabelError, LabelStore

BOX = {"label_id": 0, "xyxy_norm": [.1, .1, .8, .8], "confidence": .9}


@pytest.fixture
def resources(tmp_path):
    labels = LabelStore(tmp_path / "labels")
    project = labels.create_project("fault fixture", ["person"])
    raw = io.BytesIO(); Image.new("RGB", (20, 12), "gray").save(raw, "PNG")
    asset = labels.add_asset(project["id"], raw.getvalue(), "a.png")
    model = tmp_path / "fixture.onnx"; model.write_bytes(b"registered model fixture")
    engines = LabelEngines(); engines.register("fixture", model, ["person"])
    yield labels, project, asset, engines
    labels.close()


def submit(jobs, project, asset, key="fixture"):
    return jobs.submit(project["id"], [asset["id"]], "fixture", .4, key, "operator")


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result: return result
        time.sleep(.01)
    pytest.fail("condition did not complete before timeout")


def hung_child(monkeypatch):
    """替换的仅是子进程工作负载；仍真实启动/终止一个 Python OS 进程。"""
    original = subprocess.Popen
    processes = []
    def spawn(_args, **kwargs):
        proc = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr("visual.label_jobs.subprocess.Popen", spawn)
    return processes


def test_real_hung_child_timeout_kills_process_and_preserves_unlabeled(resources, tmp_path, monkeypatch):
    labels, project, asset, engines = resources
    processes = hung_child(monkeypatch)
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines, timeout_s=.15)
    try:
        job = submit(jobs, project, asset); jobs.start()
        until(lambda: jobs.get(job["id"])["status"] == "failed")
        assert len(processes) == 1 and processes[0].poll() is not None
        result = jobs.get(job["id"])
        assert result["failed"] == 1 and "超时" in result["items"][0]["error"]
        assert labels.get_asset(asset["id"])["revision"] == 0
    finally:
        jobs.close()


def test_running_cancel_terminates_real_child_and_is_persistent(resources, tmp_path, monkeypatch):
    labels, project, asset, engines = resources
    processes = hung_child(monkeypatch)
    db = tmp_path / "jobs.db"
    jobs = LabelJobs(db, labels, engines, timeout_s=20)
    try:
        job = submit(jobs, project, asset); jobs.start()
        until(lambda: processes)
        assert jobs.cancel(job["id"])["status"] == "canceled"
        until(lambda: processes[0].poll() is not None)
        assert labels.get_asset(asset["id"])["revision"] == 0
    finally:
        jobs.close()
    reopened = LabelJobs(db, labels, engines)
    try:
        assert reopened.get(job["id"])["status"] == "canceled"
        assert labels.get_asset(asset["id"])["targets"] == []
    finally:
        reopened.close()


def test_human_edit_during_inference_is_not_overwritten(resources, tmp_path):
    labels, project, asset, engines = resources
    entered, release = threading.Event(), threading.Event()
    def predictor(_):
        entered.set()
        if not release.wait(3): raise RuntimeError("test coordination timeout")
        return [BOX]
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines, predictor=predictor)
    try:
        job = submit(jobs, project, asset); jobs.start()
        assert entered.wait(3)
        manual = {"label_id": 0, "xyxy_norm": [.2, .2, .4, .4]}
        labels.save_draft(asset["id"], [manual], 0, "human-editor")
        release.set()
        until(lambda: jobs.get(job["id"])["status"] == "completed")
        result = jobs.get(job["id"])
        current = labels.get_asset(asset["id"])
        assert result["skipped"] == 1 and current["revision"] == 1
        assert current["producer"] == "human" and current["targets"] == [manual]
    finally:
        release.set(); jobs.close()


def test_queue_limit_and_cross_project_rejection_leave_assets_unchanged(resources, tmp_path):
    labels, project, asset, engines = resources
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines)
    try:
        wrong = labels.create_project("other", ["person"])
        with pytest.raises(LabelError) as mismatch:
            submit(jobs, wrong, asset, "wrong")
        assert mismatch.value.status == 400
        for i in range(4): submit(jobs, project, asset, f"job-{i}")
        with pytest.raises(LabelError) as full:
            submit(jobs, project, asset, "overflow")
        assert full.value.status == 429 and len(jobs.list(project["id"])) == 4
        assert labels.get_asset(asset["id"])["revision"] == 0
    finally:
        jobs.close()


def test_parallel_submit_respects_single_instance_capacity(resources, tmp_path):
    labels, project, asset, engines = resources
    jobs = LabelJobs(tmp_path / "jobs.db", labels, engines)
    def send(i):
        try: submit(jobs, project, asset, f"parallel-{i}"); return 202
        except LabelError as error: return error.status
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(send, range(12)))
        assert statuses.count(202) == 4 and statuses.count(429) == 8
    finally:
        jobs.close()


def test_demote_and_repromote_does_not_resurrect_mcp_credential(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    try:
        auth.set_user("admin", "test-only-password", "admin")
        auth.set_user("backup-admin", "test-only-password", "admin")
        token = auth.issue_mcp_token("admin", "fixture", ["read"], [])
        assert auth.verify_mcp_token(token["token"])
        auth.set_user("admin", "test-only-password", "operator")
        assert auth.verify_mcp_token(token["token"]) is None
        auth.set_user("admin", "test-only-password", "admin")
        verified = auth.verify_mcp_token(token["token"])
        assert verified is None
    finally:
        auth.close()


def test_single_queue_owner_across_instances_and_real_processes(resources, tmp_path):
    labels, project, asset, engines = resources
    path = tmp_path / "jobs.db"
    jobs = LabelJobs(path, labels, engines)
    child = """
from pathlib import Path
import sys
from visual.labels import LabelStore,LabelError
from visual.label_jobs import LabelJobs,LabelEngines
labels=LabelStore(Path(sys.argv[1]))
jobs=None
try:
    jobs=LabelJobs(Path(sys.argv[2]),labels,LabelEngines())
    print('acquired')
except LabelError as error:
    print(error.status)
finally:
    if jobs is not None: jobs.close()
    labels.close()
"""
    def probe():
        result = subprocess.run([sys.executable, "-c", child, str(labels.root), str(path)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    try:
        with pytest.raises(LabelError) as second:
            LabelJobs(path, labels, engines)
        assert second.value.status == 409
        assert probe() == "409"
    finally:
        jobs.close()
    jobs.close()  # 幂等关闭不能留下排他锁
    assert probe() == "acquired"
    reopened = LabelJobs(path, labels, engines)
    reopened.close()
