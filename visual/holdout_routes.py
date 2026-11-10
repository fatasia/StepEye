"""固定模型 × 冻结数据版本的留出验证；与 CLI 同一实现，报告不可变，不产生发布许可。

模型与数据版本路径都从服务端登记解析，HTTP 输入只允许登记 ID；
推理在独立子进程运行（同模型试测的隔离方式），报告入库后临时文件即删除。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .label_jobs import LabelEngines, fingerprint
from .labels import LabelError, LabelStore
from .accuracy_watch import AccuracyWatch, AccuracyWatchBody

ROOT = Path(__file__).resolve().parents[1]
MAX_REPORT_BYTES = 64 * 1024 * 1024


class HoldoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)
    adapter: Literal["yolo_raw", "yolo_e2e", "kairos_rtdetr", "glue_yolov4"] = "yolo_raw"
    confidence: float = Field(default=.25, gt=0, le=1, allow_inf_nan=False)
    iou: float = Field(default=.5, gt=0, le=1, allow_inf_nan=False)


class HoldoutCompareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_id: str = Field(min_length=4, max_length=100, pattern="^HE-")
    current_id: str = Field(min_length=4, max_length=100, pattern="^HE-")
    min_samples: int = Field(default=20, ge=1, le=10000, strict=True)
    min_drop: float = Field(default=.1, gt=0, le=1, allow_inf_nan=False)


class HoldoutEvaluations:
    """不可变 SQLite 报告库；触发器拒绝 UPDATE/DELETE，同一份代码服务列表与详情。"""

    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS holdout_evaluations "
                         "(id TEXT PRIMARY KEY, created REAL, body TEXT)")
        for operation in ("UPDATE", "DELETE"):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS holdout_no_{operation.lower()} "
                             f"BEFORE {operation} ON holdout_evaluations "
                             "BEGIN SELECT RAISE(ABORT,'holdout evaluations are immutable'); END")
        self.watch = AccuracyWatch(self._db)
        self._db.commit()
        self._closed = False

    def create(self, record: dict) -> None:
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
        with self._guard:
            if self._closed:
                raise ValueError("评测报告服务已关闭")
            with self._db:
                self._db.execute("INSERT INTO holdout_evaluations VALUES (?,?,?)",
                                 (record["id"], record["created_at"], raw))
                self.watch.record(record)

    def get(self, report_id: str) -> dict:
        with self._guard:
            row = self._db.execute("SELECT body FROM holdout_evaluations WHERE id=?", (report_id,)).fetchone()
            observation = self.watch.observation(report_id)
        if row is None:
            raise KeyError("评测报告不存在")
        record = json.loads(row[0])
        if observation:
            record["accuracy_watch"] = observation
        return record

    def configure_watch(self, identifier, body, actor):
        with self._guard, self._db:
            return self.watch.configure(identifier, body, actor)

    def watch_status(self, identifier):
        with self._guard:
            record = self.watch._report(identifier)
            return {"config": self.watch.config(record), "observation": self.watch.observation(identifier)}

    def list(self, limit: int = 50, offset: int = 0) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("分页范围无效")
        with self._guard:
            total = self._db.execute("SELECT COUNT(*) FROM holdout_evaluations").fetchone()[0]
            rows = self._db.execute("SELECT h.body,o.body FROM holdout_evaluations h "
                                    "LEFT JOIN accuracy_observations o ON o.report_id=h.id "
                                    "ORDER BY h.created DESC,h.id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        items = []
        for raw, observation in rows:
            value = json.loads(raw)
            heavy = {"frames", "sources", "excluded", "evaluation"}
            summary = {k: v for k, v in value.items() if k not in heavy}
            summary["frames"] = value["evaluation"]["frames"]
            summary["summary"] = value["evaluation"]["summary"]
            summary["false_positive_frames"] = value["evaluation"]["false_positive_frames"]
            summary["missed_frames"] = value["evaluation"]["missed_frames"]
            if observation:
                summary["accuracy_watch_status"] = json.loads(observation)["status"]
            items.append(summary)
        return {"total": total, "items": items}

    def close(self) -> None:
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()


class HoldoutRunner:
    """单并发执行；模型/数据快照固定 SHA 后交给子进程，与 CLI 共用 run_holdout。"""

    def __init__(self, labels: LabelStore, engines: LabelEngines, store: HoldoutEvaluations,
                 *, timeout_s: float = 600):
        self.labels, self.engines, self.store = labels, engines, store
        self.timeout_s = timeout_s
        self._slot = threading.Lock()

    def options(self) -> dict:
        projects = []
        for project in self.labels.list_projects():
            versions = [v for v in self.labels.list_versions(project["id"]) if v["val_count"] > 0]
            if versions:
                projects.append({"id": project["id"], "name": project["name"],
                                 "classes": project["classes"], "versions": versions})
        return {"models": self.engines.list(), "projects": projects, "timeout_s": self.timeout_s,
                "max_report_bytes": MAX_REPORT_BYTES}

    def run(self, body: HoldoutBody, actor: str) -> dict:
        if not self._slot.acquire(blocking=False):
            raise LabelError("已有固定评测正在运行，请稍后重试", 429)
        try:
            model = self.engines.snapshot(body.engine_id)
            version = next((v for v in self.labels.list_versions(body.project_id) if v["id"] == body.version_id), None)
            if version is None:
                raise LabelError("数据版本不存在", 404)
            dataset = self.labels.export_path(body.project_id, body.version_id)  # 内含完整性校验
            dataset_sha256 = version["zip_sha256"]
            with tempfile.TemporaryDirectory(prefix="stepeye-holdout-") as folder:
                room = Path(folder)
                spec = room / "spec.json"
                error = room / "error.json"
                output = room / "report.json"
                spec.write_text(json.dumps({
                    "dataset": str(dataset), "model": model["model_path"],
                    "output": str(output), "error": str(error),
                    "model_sha256": model["model_sha256"], "dataset_sha256": dataset_sha256,
                    "adapter": body.adapter, "confidence": body.confidence, "iou": body.iou,
                }, ensure_ascii=False), encoding="utf-8")
                # 请求走私有文件、stdin 关闭：大负载不会阻塞在管道上（阀门回放教训）。
                with tempfile.TemporaryFile() as stream:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "visual.holdout_worker", str(spec)],
                        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    try:
                        process.wait(timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise LabelError(f"评测超过{int(self.timeout_s)}秒，进程已停止", 504) from None
                if process.returncode:
                    raise LabelError(self._failure_reason(error) or
                                     "评测失败，请检查模型适配器与数据版本类别", 422)
                if not output.is_file() or output.stat().st_size > MAX_REPORT_BYTES:
                    raise LabelError("评测未产出有效报告", 422)
                try:
                    report = json.loads(output.read_text(encoding="utf-8"))
                except ValueError:
                    raise LabelError("评测报告无法解析，已拒绝入库", 422) from None
            if report.get("model_sha256") != model["model_sha256"] or report.get("dataset_sha256") != dataset_sha256:
                raise LabelError("评测报告指纹不匹配，已拒绝入库", 422)
            if fingerprint(Path(model["model_path"])) != model["model_sha256"]:
                raise LabelError("评测期间模型文件发生变化，报告已丢弃，请重试", 409)
            record = {**report, "id": "HE-" + uuid.uuid4().hex,
                      "created_at": time.time(), "actor": actor,
                      "engine_id": body.engine_id, "engine_name": model["name"],
                      "project_id": body.project_id, "version_id": body.version_id}
            self.store.create(record)
            return self.store.get(record["id"])
        finally:
            self._slot.release()

    @staticmethod
    def _failure_reason(error: Path) -> str:
        try:
            return json.loads(error.read_text(encoding="utf-8")).get("detail", "")[:500]
        except (OSError, ValueError):
            return ""


def router(runner: HoldoutRunner) -> APIRouter:
    api = APIRouter()

    @api.get("/api/evaluations/holdout/options")
    def options() -> dict:
        return runner.options()

    @api.post("/api/evaluations/holdout", status_code=201)
    def create(body: HoldoutBody, request: Request) -> dict:
        try:
            return runner.run(body, (getattr(request.state, "user", None) or {}).get("username", "local"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.get("/api/evaluations/holdout")
    def listing(limit: int = 50, offset: int = 0) -> dict:
        try:
            return runner.store.list(limit, offset)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.get("/api/evaluations/holdout/{report_id}")
    def detail(report_id: str) -> dict:
        try:
            return runner.store.get(report_id)
        except KeyError:
            raise HTTPException(404, "评测报告不存在") from None

    @api.post("/api/evaluations/holdout/compare")
    def compare(body: HoldoutCompareBody) -> dict:
        from .accuracy_monitor import compare_reports
        try:
            return compare_reports(runner.store.get(body.baseline_id), runner.store.get(body.current_id),
                                   min_samples=body.min_samples, min_drop=body.min_drop)
        except KeyError:
            raise HTTPException(404, "评测报告不存在") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.get("/api/evaluations/holdout/{report_id}/watch")
    def watch_status(report_id: str):
        try:
            return runner.store.watch_status(report_id)
        except KeyError:
            raise HTTPException(404, "评测报告不存在") from None

    @api.post("/api/evaluations/holdout/{report_id}/watch")
    def watch_config(report_id: str, body: AccuracyWatchBody, request: Request):
        try:
            actor = (getattr(request.state, "user", None) or {}).get("username", "local")
            return runner.store.configure_watch(report_id, body.model_dump(), actor)
        except KeyError:
            raise HTTPException(404, "评测报告不存在") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return api
