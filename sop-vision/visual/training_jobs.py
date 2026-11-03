"""Persistent single-worker training queue. HTTP supplies IDs, never commands or paths.

Interrupted work is marked interrupted on startup rather than silently spending GPU
again. Only validated candidate artifacts enter the model catalog; never production.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile

from .label_jobs import _claim_worker, fingerprint
from .labels import LabelError

ACTIVE = {"queued", "running"}


class TrainingJobs:
    def __init__(self, root, labels, engines, *, python=None, device="cpu", timeout_s=14400):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.labels, self.engines = labels, engines
        self.python, self.device, self.timeout_s = python, device, timeout_s
        self._guard = threading.RLock()
        self._owner = _claim_worker(self.root / "training.db")
        self._db = sqlite3.connect(self.root / "training.db", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project TEXT, idem TEXT, body TEXT, UNIQUE(project,idem))")
        self._thread = self._process = None
        self._closed = self._released = False
        # Recover metadata, not execution; repeated training must be an explicit action.
        for (raw,) in self._db.execute("SELECT body FROM jobs").fetchall():
            job = json.loads(raw)
            if job["status"] in ACTIVE:
                job.update(status="interrupted", error="上次服务中断，请检查产物后重新创建任务")
                self._save(job)
            elif job["status"] == "candidate":
                self._register(job)
        self._db.commit()

    @classmethod
    def from_env(cls, root, labels, engines):
        from .llm_provider import _default_env_path, load_env_file
        env = load_env_file(_default_env_path())
        python = os.getenv("STEPEYE_TRAIN_PYTHON", env.get("STEPEYE_TRAIN_PYTHON", ""))
        device = os.getenv("STEPEYE_TRAIN_DEVICE", env.get("STEPEYE_TRAIN_DEVICE", "cpu"))
        if device not in {"cpu", "0"}:
            raise ValueError("STEPEYE_TRAIN_DEVICE 当前仅支持cpu或首张GPU编号0")
        return cls(root, labels, engines, python=python or None, device=device)

    def _save(self, job):
        self._db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)",
            (job["id"], job["project_id"], job["idempotency_key"], json.dumps(job, ensure_ascii=False)))
        self._db.commit()

    def _load(self, jid):
        row = self._db.execute("SELECT body FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise LabelError("训练任务不存在", 404)
        return json.loads(row[0])

    @staticmethod
    def _public(job):
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def get(self, jid):
        with self._guard:
            return self._public(self._load(jid))

    def list(self, pid):
        self.labels.get_project(pid)
        with self._guard:
            rows = self._db.execute("SELECT body FROM jobs WHERE project=? ORDER BY rowid DESC LIMIT 30", (pid,)).fetchall()
            return [self._public(json.loads(row[0])) for row in rows]

    def capabilities(self):
        return {"configured": bool(self.python), "device": self.device,
                "defaults": {"epochs": 30, "batch": 4, "imgsz": 640},
                "reason": "" if self.python else "管理员需先配置隔离训练环境 STEPEYE_TRAIN_PYTHON"}

    def submit(self, pid, engine_id, idem, actor):
        if not isinstance(idem, str) or not 1 <= len(idem) <= 100:
            raise LabelError("需要有效的幂等键")
        with self._guard:
            if self._closed:
                raise LabelError("训练服务正在停止", 409)
            prior = self._db.execute("SELECT body FROM jobs WHERE project=? AND idem=?", (pid, idem)).fetchone()
            if prior:
                job = json.loads(prior[0])
                if job["engine_id"] != engine_id:
                    raise LabelError("幂等键已绑定另一模型", 409)
                return self._public(job)
            if not self.python:
                raise LabelError(self.capabilities()["reason"], 409)
            active = sum(json.loads(row[0])["status"] in ACTIVE for row in self._db.execute("SELECT body FROM jobs"))
            if active >= 4:
                raise LabelError("训练队列已满", 429)
            engine = self.engines.snapshot(engine_id)
            # Only paired checkpoints already registered by the local administrator.
            base = Path(engine["model_path"]).with_suffix(".pt")
            if not base.is_file() or base.is_symlink():
                raise LabelError("当前模型没有已登记的本机训练权重，不能从ONNX继续训练", 409)
            version = self.labels.create_version(pid, actor=actor)
            if not version["train_count"] or not version["val_count"]:
                raise LabelError("需要至少两个独立来源组的已确认样本，分别用于训练和验证", 409)
            job = {"id": "TJ-" + uuid.uuid4().hex, "project_id": pid, "engine_id": engine_id,
                   "idempotency_key": idem, "actor": actor, "status": "queued", "created_at": time.time(),
                   "version_id": version["id"], "archive_sha256": version["zip_sha256"],
                   "base_sha256": fingerprint(base), "_base": str(base), "error": "",
                   "classes": self.labels.get_project(pid)["classes"]}
            self._save(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="training", daemon=True)
                self._thread.start()
            return self._public(job)

    def _prepare(self, job):
        folder = self.root / job["id"]
        folder.mkdir(exist_ok=False)
        data = folder / "data"
        data.mkdir()
        archive = self.labels.export_path(job["project_id"], job["version_id"])
        if fingerprint(archive) != job["archive_sha256"]:
            raise LabelError("数据版本指纹变化", 409)
        # Even our immutable export is extracted conservatively, never via extractall.
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if len(infos) > 100000 or sum(i.file_size for i in infos) > 2 * 1024**3:
                raise LabelError("数据版本超过单次训练导入限制")
            for info in infos:
                with self._guard:
                    if self._closed or self._load(job["id"])["status"] == "cancelled":
                        raise LabelError("任务已取消", 409)
                p = PurePosixPath(info.filename)
                if p.is_absolute() or ".." in p.parts or "\\" in info.filename or ":" in info.filename:
                    raise LabelError("数据版本路径无效")
                if info.is_dir():
                    continue
                dest = data.joinpath(*p.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as source, dest.open("xb") as target:
                    shutil.copyfileobj(source, target)
        base = folder / "base.pt"
        shutil.copyfile(job["_base"], base)
        if fingerprint(base) != job["base_sha256"]:
            raise LabelError("基座模型指纹变化", 409)
        from .training_worker import inspect_training_dataset
        checked = inspect_training_dataset(data / "dataset.yaml")
        spec = {"dataset_yaml": str(data / "dataset.yaml"), "base_model": str(base),
                "output_dir": str(folder / "output"), "epochs": 30, "batch": 4, "imgsz": 640,
                "device": self.device, "base_sha256": job["base_sha256"],
                "dataset_version": job["version_id"], "dataset_sha256": checked["dataset_sha256"]}
        path = folder / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path, spec

    def _execute(self, job):
        path, spec = self._prepare(job)
        # Logs stay local and bounded by the worker's normal training output; not served over HTTP.
        with (path.parent / "worker.log").open("wb") as log:
            with self._guard:
                if self._closed or self._load(job["id"])["status"] == "cancelled":
                    return None
                proc = subprocess.Popen([self.python, "-m", "visual.training_worker", "--spec", str(path)],
                    cwd=Path(__file__).resolve().parents[1], stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._process = proc
            try:
                proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
                raise LabelError("训练超过时间上限，进程已停止", 504) from None
            finally:
                with self._guard:
                    self._process = None
            if proc.returncode:
                raise LabelError("训练失败，请管理员检查本机任务日志", 422)
        output = Path(spec["output_dir"])
        result_path = output / "result.json"
        if not result_path.is_file() or result_path.stat().st_size > 1024**2:
            raise LabelError("训练结果缺失或过大", 422)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        model = Path(result.get("model_path", ""))
        if not model.is_absolute():
            model = output / model
        if (not model.resolve().is_relative_to(output.resolve()) or model.is_symlink() or model.suffix != ".onnx"
                or not model.is_file() or fingerprint(model) != result.get("model_sha256")
                or result.get("base_sha256") != spec["base_sha256"]
                or result.get("dataset_sha256") != spec["dataset_sha256"]
                or result.get("classes") != job["classes"] or result.get("status") != "candidate"):
            raise LabelError("训练产物指纹或类别不匹配", 422)
        return {"model_sha256": result["model_sha256"], "dataset_sha256": spec["dataset_sha256"],
                "metrics": result.get("metrics", {}), "_model": str(model.resolve())}

    def _register(self, job):
        model = Path(job.get("_model", ""))
        if model.is_file() and fingerprint(model) == job.get("model_sha256"):
            self.engines.register("trained-" + job["id"], model, job["classes"], "训练候选 · " + job["project_id"],
                                  metadata={"expected_sha256": job["model_sha256"], "usage": "candidate"})

    def _run(self):
        while True:
            with self._guard:
                jobs = [json.loads(row[0]) for row in self._db.execute("SELECT body FROM jobs ORDER BY rowid")]
                job = next((j for j in jobs if j["status"] == "queued"), None)
                if self._closed or job is None:
                    self._thread = None
                    if self._closed:
                        self._release_locked()
                    return
                job.update(status="running", started_at=time.time())
                self._save(job)
            try:
                result = self._execute(job)
                if result is not None:
                    job.update(result, status="candidate", finished_at=time.time())
            except Exception as exc:
                # Do not return exception text: trainer traces may contain host paths or credentials.
                job.update(status="failed", error=str(exc) if isinstance(exc, LabelError) else "训练失败，请检查任务日志",
                           finished_at=time.time())
            with self._guard:
                current = self._load(job["id"])
                if current["status"] != "cancelled":
                    self._save(job)
                    if job["status"] == "candidate":
                        self._register(job)

    def cancel(self, jid):
        with self._guard:
            job = self._load(jid)
            if job["status"] in ACTIVE:
                was_running = job["status"] == "running"
                job.update(status="cancelled", finished_at=time.time())
                self._save(job)
                if was_running and self._process is not None:
                    self._process.kill()
            return self._public(job)

    def _release_locked(self):
        if not self._released:
            self._released = True
            self._db.close()
            self._owner.close()

    def close(self):
        with self._guard:
            if self._closed:
                return
            self._closed = True
            for (raw,) in self._db.execute("SELECT body FROM jobs").fetchall():
                job = json.loads(raw)
                if job["status"] in ACTIVE:
                    job.update(status="cancelled", finished_at=time.time())
                    self._save(job)
            if self._process is not None:
                self._process.kill()
            thread = self._thread
        if thread:
            thread.join(timeout=10)
        # A preflight may still be reading an immutable archive. Defer closing until it retires.
        if thread and thread.is_alive():
            return
        with self._guard:
            self._release_locked()
