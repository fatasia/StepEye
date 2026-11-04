"""持久单工人预标注队列。机器结果只写建议，图像/模型来自受信登记，单图进程可终止。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .labels import LabelError, LabelStore


def _claim_worker(path: Path):
    """原生跨进程排他锁；队列只允许一个服务实例消费，不是多工人调度器。"""
    handle = path.with_suffix(".worker.lock").open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        raise LabelError("该标注队列已由另一服务实例占用；使用不同数据目录或先停止旧服务", 409) from None


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class LabelEngines:
    """仅本机管理员配置的模型；HTTP 输入不能指定文件路径或下载 URL。"""
    def __init__(self):
        self._models: dict[str, dict] = {}
        self._guard = threading.RLock()

    def register(self, engine_id: str, model_path: Path, classes: list[str], name: str = "",
                 *, metadata: dict | None = None) -> None:
        with self._guard:
            self._models[engine_id] = {**(metadata or {}), "id": engine_id, "name": name or engine_id,
                                       "model_path": str(model_path.resolve()), "classes": list(classes)}

    def list(self) -> list[dict]:
        out = []
        with self._guard:
            registered = list(self._models.values())
        for m in registered:
            available = Path(m["model_path"]).is_file() and importlib.util.find_spec("onnxruntime") is not None
            out.append({k: v for k, v in m.items() if k != "model_path"} | {
                "available": available, "reason": "" if available else "缺少模型或 onnxruntime；可先手工标注"})
        return out

    def snapshot(self, engine_id: str) -> dict:
        with self._guard:
            if engine_id not in self._models:
                raise LabelError("预标注引擎未登记，请选现有引擎或手工框选", 404)
            m = dict(self._models[engine_id])
        if not Path(m["model_path"]).is_file():
            raise LabelError("模型未安装，可先手工框选", 409)
        m["model_sha256"] = fingerprint(Path(m["model_path"]))
        if m.get("expected_sha256") and m["model_sha256"] != m["expected_sha256"]:
            raise LabelError("模型文件与登记指纹不一致，请重新安装已审核权重", 409)
        from .model_signature import verify_model_policy
        try:
            m["signature"] = verify_model_policy(Path(m["model_path"]))
        except ValueError as exc:
            raise LabelError(str(exc), 409) from exc
        return m


class LabelJobs:
    def __init__(self, db_path: Path, labels: LabelStore, engines: LabelEngines,
                 *, timeout_s: float = 30, predictor=None):
        self.labels, self.engines = labels, engines
        self.timeout_s, self._predictor = timeout_s, predictor
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._worker_lock = _claim_worker(db_path)
        self._closed = False
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS label_jobs (id TEXT PRIMARY KEY, project TEXT, "
                         "idem TEXT, signature TEXT, body TEXT, UNIQUE(project,idem))")
        self._db.commit()
        # 进程退出前未落账的单图重新排队；由资产 provenance 防止重复写修订。
        for (raw,) in self._db.execute("SELECT body FROM label_jobs").fetchall():
            job = json.loads(raw)
            if job["status"] == "running":
                job["status"] = "queued"
                for item in job["items"]:
                    if item["status"] == "running":
                        item["status"] = "queued"
                self._save(job)
        self._started = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def _load(self, jid: str) -> dict:
        row = self._db.execute("SELECT body FROM label_jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise LabelError("预标注任务不存在", 404)
        return json.loads(row[0])

    def _save(self, job: dict) -> None:
        items = job["items"]
        for key in ("suggested", "no_targets", "skipped", "failed"):
            job[key] = sum(i["status"] == key for i in items)
        job["processed"] = sum(i["status"] not in ("queued", "running") for i in items)
        self._db.execute("UPDATE label_jobs SET body=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job["id"]))
        self._db.commit()

    @staticmethod
    def _public(job: dict) -> dict:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def get(self, jid: str) -> dict:
        with self._lock:
            return self._public(self._load(jid))

    def list(self, project_id: str) -> list[dict]:
        self.labels.get_project(project_id)
        with self._lock:
            rows = self._db.execute("SELECT body FROM label_jobs WHERE project=? ORDER BY rowid DESC LIMIT 30",
                                    (project_id,)).fetchall()
        return [self._public(json.loads(r[0])) for r in rows]

    def submit(self, project_id: str, asset_ids: list[str], engine_id: str, threshold: float,
               idempotency_key: str, actor: str) -> dict:
        if not 1 <= len(asset_ids) <= 200 or len(set(asset_ids)) != len(asset_ids):
            raise LabelError("每批选择1–200张不同图片", 400)
        if not math.isfinite(threshold) or not 0.05 <= threshold <= 0.95:
            raise LabelError("置信阈值必须在0.05–0.95", 400)
        if not 1 <= len(idempotency_key) <= 100:
            raise LabelError("需要1–100字符的幂等键", 400)
        project = self.labels.get_project(project_id)
        engine = self.engines.snapshot(engine_id)
        if not set(project["classes"]).issubset(engine["classes"]):
            raise LabelError("该模型不认识项目中的全部类别，请手工标注或登记匹配模型", 409)
        signature = hashlib.sha256(json.dumps([asset_ids, engine, threshold], sort_keys=True).encode()).hexdigest()
        with self._lock:
            prior = self._db.execute("SELECT id,signature FROM label_jobs WHERE project=? AND idem=?",
                                     (project_id, idempotency_key)).fetchone()
            if prior:
                if prior[1] != signature:
                    raise LabelError("幂等键已绑定另一组图片/模型/参数", 409)
                return self._public(self._load(prior[0]))
            active = self._db.execute("SELECT count(*) FROM label_jobs WHERE json_extract(body,'$.status') IN ('queued','running')").fetchone()[0]
            if active >= 4:
                raise LabelError("已有4个任务排队，请稍后再试", 429)
            items = []
            for aid in asset_ids:
                asset = self.labels.get_asset(aid)
                if asset["project_id"] != project_id:
                    raise LabelError("图片不属于该项目", 400)
                items.append({"asset_id": aid, "expected_revision": asset["revision"], "status": "queued"})
            job = {"id": uuid.uuid4().hex, "project_id": project_id, "engine_id": engine_id,
                   "engine_snapshot": {k: v for k, v in engine.items() if k != "model_path"},
                   "threshold": threshold, "actor": actor, "created_at": time.time(), "status": "queued",
                   "total": len(items), "items": items, "_engine": engine, "_project_classes": project["classes"]}
            self._db.execute("INSERT INTO label_jobs VALUES (?,?,?,?,?)",
                             (job["id"], project_id, idempotency_key, signature, "{}"))
            self._save(job)
            self._wake_if_needed()
        return self.get(job["id"])

    def cancel(self, jid: str) -> dict:
        with self._lock:
            job = self._load(jid)
            if job["status"] in ("queued", "running"):
                job["status"] = "canceled"
                self._save(job)
                if self._process is not None and self._process.poll() is None and getattr(self._process, "_label_job", None) == jid:
                    self._process.terminate()
            return self._public(job)

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._wake_if_needed()

    def _wake_if_needed(self) -> None:
        # 持锁检查队列与线程；提交与最后一个任务退出不会错过唤醒。
        if self._started and not self._stop.is_set() and self._thread is None and self._db.execute(
                "SELECT 1 FROM label_jobs WHERE json_extract(body,'$.status')='queued' LIMIT 1").fetchone():
            self._thread = threading.Thread(target=self._run, name="label-worker", daemon=True)
            self._thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
            thread = self._thread
        if thread:
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("预标注线程尚未退出")
        with self._lock:
            self._db.close()
            self._worker_lock.close()
            self._closed = True

    def _infer(self, job: dict, asset: dict) -> list[dict]:
        engine = job["_engine"]
        if fingerprint(Path(engine["model_path"])) != engine["model_sha256"]:
            raise LabelError("模型文件已变更，请重新创建任务", 409)
        image_path = self.labels.image_path(asset["id"])
        if fingerprint(image_path) != asset["image_sha256"]:
            raise LabelError("图像完整性校验失败，拒绝预标注", 422)
        spec = {**engine, "image_path": str(image_path),
                "project_classes": job["_project_classes"], "threshold": job["threshold"]}
        if self._predictor:
            return self._predictor(spec)
        proc = subprocess.Popen([sys.executable, "-m", "visual.label_predict"],
                                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with self._lock:
            self._process = proc
            proc._label_job = job["id"]
            if self._stop.is_set() or self._load(job["id"])["status"] == "canceled":
                proc.kill()
        try:
            out, _ = proc.communicate(json.dumps(spec).encode(), timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate()
            raise LabelError("单图推理超时；已停止该进程，可手工框选或新建重试任务", 504) from None
        finally:
            with self._lock:
                self._process = None
        if proc.returncode or len(out) > 256 * 1024:
            raise LabelError("预标注进程失败；请检查模型布局/运行库，可先手工标注", 422)
        targets = json.loads(out)["targets"]
        if not isinstance(targets, list) or len(targets) > 500:
            raise LabelError("预标注结果格式不合法", 422)
        return targets

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                row = self._db.execute("SELECT body FROM label_jobs WHERE json_extract(body,'$.status')='queued' ORDER BY rowid LIMIT 1").fetchone()
                job = json.loads(row[0]) if row else None
                if job:
                    job["status"] = "running"; self._save(job)
                else:
                    self._thread = None
                    return
            self._execute(job)

    def _execute(self, job: dict) -> None:
        for item in job["items"]:
            if item["status"] not in ("queued", "running"):
                continue
            with self._lock:
                current = self._load(job["id"])
                if self._stop.is_set() or current["status"] == "canceled":
                    return
                item["status"] = "running"; self._save(job)
            try:
                asset = self.labels.get_asset(item["asset_id"])
                if (asset.get("provenance") or {}).get("job_id") == job["id"]:
                    item["status"] = "suggested" if asset["targets"] else "no_targets"
                elif asset["revision"] != item["expected_revision"] or asset["state"] in ("confirmed", "background"):
                    item.update(status="skipped", error="图片已被编辑或确认，保留现有结果")
                else:
                    targets = self._infer(job, asset)
                    with self._lock:
                        current = self._load(job["id"])
                        if self._stop.is_set() or current["status"] == "canceled":
                            return
                        if fingerprint(self.labels.image_path(asset["id"])) != asset["image_sha256"]:
                            raise LabelError("图像在处理期间发生变化，拒绝写入建议", 422)
                        self.labels.save_draft(asset["id"], targets, item["expected_revision"], job["actor"],
                            producer="model", provenance={"job_id": job["id"], "engine_id": job["engine_id"],
                            "model_sha256": job["_engine"]["model_sha256"], "threshold": job["threshold"]})
                    item["status"] = "suggested" if targets else "no_targets"
            except LabelError as e:
                item.update(status="skipped" if e.status == 409 else "failed", error=str(e)[:200])
            except Exception:
                item.update(status="failed", error="处理失败，可手工标注或创建新任务重试")
            with self._lock:
                if self._stop.is_set() or self._load(job["id"])["status"] == "canceled":
                    return
                self._save(job)
        with self._lock:
            if self._stop.is_set() or self._load(job["id"])["status"] == "canceled":
                return
            job["status"] = "failed" if job["failed"] == job["total"] else "partial" if job["failed"] else "completed"
            job["finished_at"] = time.time()
            self._save(job)
