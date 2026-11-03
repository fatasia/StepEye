"""模型发布账本:审批→激活→回滚;append-only 修订,不触 yaml、不改引擎注册。

账本只记录"某 scene 应使用哪个 engine+sha"。激活/回滚是账本状态变化;
真实生效路径(工位重载读取账本)待接入,本模块不做任何半吊子接线。
评测不等于审批:candidate 必须绑定一份 publication=not_requested 的 HE 检测验证报告,
且 model_sha256/engine_id/adapter/classes 与报告一致;审批是独立的 admin 动作。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .detection_eval import validated_classes


def _validated_fields(engine_id, model_sha256, evaluation_id, adapter, classes):
    """Shared candidate field contract for the HTTP layer and direct store use."""
    if not isinstance(engine_id, str) or not 1 <= len(engine_id) <= 100:
        raise ValueError("engine_id 无效")
    if not isinstance(model_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", model_sha256):
        raise ValueError("model_sha256 必须为64位小写十六进制")
    if not isinstance(evaluation_id, str) or not evaluation_id.startswith("HE-") or len(evaluation_id) > 100:
        raise ValueError("evaluation_id 必须为 HE- 前缀的评测报告ID")
    if adapter not in ("yolo_raw", "yolo_e2e"):
        raise ValueError("adapter 不受支持")
    return validated_classes(classes)


def create_candidate(releases, evaluations, *, engine_id, model_sha256, evaluation_id,
                     adapter, classes, actor):
    """校验 HE 报告绑定关系后写入 candidate;报告缺失抛 KeyError(路由映射 404)。"""
    classes = _validated_fields(engine_id, model_sha256, evaluation_id, adapter, classes)
    report = evaluations.get(evaluation_id)
    if report.get("kind") != "detection_validation":
        raise ValueError("评测报告不是检测验证报告")
    if report.get("model_sha256") != model_sha256:
        raise ValueError("candidate 的 model_sha256 与评测报告不一致")
    if report.get("engine_id") != engine_id:
        raise ValueError("candidate 的 engine_id 与评测报告不一致")
    if report.get("adapter") != adapter:
        raise ValueError("candidate 的 adapter 与评测报告不一致")
    if report.get("publication") != "not_requested":
        raise ValueError("该评测报告已进入发布流程;审批是独立动作,需未发布的评测")
    if list((report.get("evaluation") or {}).get("classes") or {}) != classes:
        raise ValueError("candidate 类别表与评测报告类别表不一致")
    return releases.create(engine_id=engine_id, model_sha256=model_sha256, evaluation_id=evaluation_id,
                           adapter=adapter, classes=classes, actor=actor)


class ModelReleases:
    """不可变 SQLite 账本(WAL):每次状态迁移追加一个修订行,当前状态=最高修订。

    状态机:candidate → approved → active(scene 作用域,同 scene 唯一)→
    rolled_back / superseded;回滚=切回上一 active,并把 replaced_release_id 对称回写。
    """

    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS model_releases "
                         "(revision_id TEXT PRIMARY KEY, release_id TEXT, revision INTEGER, "
                         "created REAL, body TEXT, UNIQUE(release_id, revision))")
        self._db.execute("CREATE INDEX IF NOT EXISTS model_releases_by_release "
                         "ON model_releases (release_id, revision)")
        for operation in ("UPDATE", "DELETE"):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS model_release_no_{operation.lower()} "
                             f"BEFORE {operation} ON model_releases "
                             "BEGIN SELECT RAISE(ABORT,'model releases are immutable'); END")
        self._db.commit()
        self._closed = False

    @contextmanager
    def _transaction(self):
        with self._guard:
            if self._closed:
                raise ValueError("模型发布账本服务已关闭")
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield self._db
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def _current_locked(self, release_id):
        row = self._db.execute(
            "SELECT body FROM model_releases WHERE release_id=? ORDER BY revision DESC LIMIT 1",
            (release_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def _active_on_locked(self, scene):
        rows = self._db.execute(
            "SELECT body FROM model_releases WHERE revision = (SELECT MAX(revision) "
            "FROM model_releases m WHERE m.release_id = model_releases.release_id)").fetchall()
        for (raw,) in rows:
            record = json.loads(raw)
            if record["status"] == "active" and record.get("scene") == scene:
                return record
        return None

    def _insert_locked(self, record):
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False)
        self._db.execute("INSERT INTO model_releases VALUES (?,?,?,?,?)",
                         (uuid.uuid4().hex, record["id"], record["revision"],
                          record["updated_at"], raw))

    def _bump(self, current, *, status, action, actor, extra=None):
        now = time.time()
        transition = {"action": action, "actor": actor, "at": now}
        transition.update(extra or {})
        return {**current, "revision": current["revision"] + 1, "status": status,
                "updated_at": now, "updated_by": actor, "last_transition": transition}

    def create(self, *, engine_id, model_sha256, evaluation_id, adapter, classes, actor):
        classes = _validated_fields(engine_id, model_sha256, evaluation_id, adapter, classes)
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 100:
            raise ValueError("actor 无效")
        now = time.time()
        record = {"id": "MR-" + uuid.uuid4().hex, "revision": 1, "status": "candidate",
                  "engine_id": engine_id, "model_sha256": model_sha256,
                  "evaluation_id": evaluation_id, "adapter": adapter, "classes": classes,
                  "created_at": now, "created_by": actor, "updated_at": now, "updated_by": actor,
                  "approved_by": None, "approved_at": None, "scene": None,
                  "activated_at": None, "activated_by": None, "replaced_release_id": None,
                  "last_transition": {"action": "candidate", "actor": actor, "at": now}}
        with self._transaction():
            self._insert_locked(record)
        return record

    def approve(self, release_id, actor):
        with self._transaction():
            current = self._current_locked(release_id)
            if current is None:
                raise KeyError("发布记录不存在")
            if current["status"] != "candidate":
                raise ValueError("仅 candidate 状态可审批")
            record = self._bump(current, status="approved", action="approve", actor=actor)
            record.update(approved_by=actor, approved_at=record["updated_at"])
            self._insert_locked(record)
        return record

    def activate(self, release_id, scene, actor):
        if not isinstance(scene, str) or not scene.strip() or len(scene) > 160:
            raise ValueError("scene 无效")
        with self._transaction():
            current = self._current_locked(release_id)
            if current is None:
                raise KeyError("发布记录不存在")
            if current["status"] != "approved":
                raise ValueError("仅 approved 状态可激活")
            previous = self._active_on_locked(scene)
            now = time.time()
            record = self._bump(current, status="active", action="activate", actor=actor,
                                extra={"scene": scene})
            record.update(scene=scene, activated_at=now, activated_by=actor,
                          replaced_release_id=previous["id"] if previous else None)
            self._insert_locked(record)
            if previous is not None:  # 同 scene 只保留一个 active,旧版记 superseded
                superseded = self._bump(previous, status="superseded", action="supersede",
                                        actor=actor, extra={"scene": scene})
                self._insert_locked(superseded)
        return record

    def rollback(self, release_id, actor):
        with self._transaction():
            current = self._current_locked(release_id)
            if current is None:
                raise KeyError("发布记录不存在")
            if current["status"] != "active":
                raise ValueError("仅 active 状态可回滚")
            target_id = current.get("replaced_release_id")
            if not target_id:
                raise ValueError("没有更早的 active 版本可回滚")
            target = self._current_locked(target_id)
            if target is None:  # append-only 下不可达;防御账本被外力破坏
                raise ValueError("账本缺少上一版本记录,拒绝自动回滚")
            rolled = self._bump(current, status="rolled_back", action="rollback", actor=actor,
                                extra={"scene": current["scene"], "restored": target_id})
            self._insert_locked(rolled)
            restored = self._bump(target, status="active", action="restore", actor=actor,
                                  extra={"scene": target["scene"]})
            restored.update(activated_at=restored["updated_at"], activated_by=actor,
                            replaced_release_id=current["id"])
            self._insert_locked(restored)
        return rolled

    def get(self, release_id):
        with self._guard:
            if self._closed:
                raise ValueError("模型发布账本服务已关闭")
            current = self._current_locked(release_id)
        if current is None:
            raise KeyError("发布记录不存在")
        return current

    def list(self, limit: int = 50, offset: int = 0) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("分页范围无效")
        current_revision = ("SELECT body FROM model_releases WHERE revision = (SELECT MAX(revision) "
                            "FROM model_releases m WHERE m.release_id = model_releases.release_id)")
        with self._guard:
            if self._closed:
                raise ValueError("模型发布账本服务已关闭")
            total = self._db.execute("SELECT COUNT(DISTINCT release_id) FROM model_releases").fetchone()[0]
            rows = self._db.execute(f"{current_revision} ORDER BY created DESC, release_id LIMIT ? OFFSET ?",
                                    (limit, offset)).fetchall()
        return {"total": total, "items": [json.loads(raw) for (raw,) in rows]}

    def transitions(self, release_id) -> list[dict]:
        with self._guard:
            if self._closed:
                raise ValueError("模型发布账本服务已关闭")
            rows = self._db.execute(
                "SELECT body FROM model_releases WHERE release_id=? ORDER BY revision ASC",
                (release_id,)).fetchall()
        if not rows:
            raise KeyError("发布记录不存在")
        items = []
        for (raw,) in rows:
            record = json.loads(raw)
            items.append({"revision": record["revision"], "status": record["status"],
                          **record["last_transition"]})
        return items

    def close(self) -> None:
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()
