"""拍照即检服务(V 系列):场景配置 → 异步队列 → 大模型判定 → SQLite + WebSocket 推送。

设计约束:
- 场景在 YAML 里声明,enabled=false 的场景拒绝提交(默认只启用验证过的组合,ROADMAP 纪律)。
- 判定在独立线程排队执行,HTTP 提交立即返回 pending,前端轮询/WS 收完成事件。
- 单图走一次判定;first_article 模式读参考图目录,两步判定(先列差异,再按规则判)。
- 判定失败(调用失败/JSON 不合法)落 failed 状态,不抛出、不崩溃;原图落盘可复核。
"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import yaml

from .llm_provider import VLMProvider, extract_json

MAX_IMAGES = 10
# 注意:模板里的 JSON 示例用双花括号转义,否则 .format() 会把字段当占位符
DIFF_PROMPT = (
    "前 {n_ok} 张是首件 OK 参考图,最后 1 张是待检图。列出待检图与 OK 参考图的所有可见差异"
    '(按位置描述)。只输出JSON:{{"differences":["..."]}}'
)
VERDICT_PROMPT = (
    "前 {n_ok} 张是首件 OK 参考图,中间 {n_ng} 张是典型 NG 参考图,最后 1 张是待检图。"
    "判定规则:与 OK 参考共有的特征属于正常工艺特征,不算缺陷;只有 OK 基准中不存在、"
    "且与 NG 参考同类的缺失/异常才判 NG。待检差异:{differences}\n"
    '{{"verdict":"PASS|NG|UNCERTAIN","confidence":0到1,"reason":"不超过30字"}}'
)


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    prompt: str = ""  # single 模式必填;first_article 用内置两步提示词
    enabled: bool = False
    mode: str = "single"  # single | first_article
    ok_refs: tuple[str, ...] = ()
    ng_refs: tuple[str, ...] = ()


def load_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for s in raw.get("scenarios", []):
        out.append(Scenario(
            id=str(s["id"]), name=str(s.get("name", s["id"])), prompt=str(s.get("prompt", "")).strip(),
            enabled=bool(s.get("enabled", False)), mode=str(s.get("mode", "single")),
            ok_refs=tuple(s.get("ok_refs", ())), ng_refs=tuple(s.get("ng_refs", ())),
        ))
    ids = [s.id for s in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"场景 id 重复: {ids}")
    for s in out:
        # single 模式的判定提示词来自配置;first_article 用内置两步提示词,无需配置
        if s.mode == "single" and not s.prompt:
            raise ValueError(f"场景 {s.id} 缺少提示词")
        if s.mode not in ("single", "first_article"):
            raise ValueError(f"场景 {s.id} 的 mode 必须是 single 或 first_article")
        if s.mode == "first_article" and (not s.ok_refs or not s.ng_refs):
            raise ValueError(f"场景 {s.id} 是 first_article 模式,必须配置 ok_refs 与 ng_refs")
    return out


class InspectionStore:
    """任务落库,接口对齐 CycleStore(SQLite + 锁 + body JSON)。"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS inspection_tasks (id TEXT PRIMARY KEY, scenario TEXT, status TEXT,"
            " verdict TEXT, confidence REAL, created TEXT, body TEXT)"
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def save(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO inspection_tasks VALUES (?,?,?,?,?,?,?)",
                (rec["id"], rec["scenario"], rec["status"], rec.get("verdict"),
                 rec.get("confidence"), rec["created"], json.dumps(rec, ensure_ascii=False)),
            )
            self._db.commit()

    def list(self, limit: int = 50, status: str | None = None, scenario: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT body FROM inspection_tasks WHERE 1=1", []
        for col, val in (("status", status), ("scenario", scenario)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        q += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM inspection_tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def stats(self) -> dict[str, Any]:
        with self._lock:
            rows = self._db.execute("SELECT status, verdict, COUNT(*) FROM inspection_tasks GROUP BY status, verdict").fetchall()
        total = sum(n for _, _, n in rows)
        verdicts: dict[str, int] = {}
        for status, verdict, n in rows:
            if status == "done":
                verdicts[verdict or "?"] = verdicts.get(verdict or "?", 0) + n
        return {"total": total, "by_status": {s: sum(n for st, _, n in rows if st == s) for s, _, _ in rows},
                "by_verdict": verdicts}


class InspectionService:
    def __init__(self, scenarios: list[Scenario], store: InspectionStore, images_root: Path,
                 provider: VLMProvider, publish: Callable[[dict[str, Any]], None] | None = None,
                 refs_root: Path | None = None):
        self.scenarios = {s.id: s for s in scenarios}
        self.store = store
        self.images_root = images_root
        self.refs_root = refs_root or images_root.parent
        self.provider = provider
        self.publish = publish or (lambda msg: None)
        self._q: queue.Queue[dict[str, Any]] = queue.Queue()
        self._guard = threading.Lock()
        self._started = False
        self._stopped = False
        self._close_store = False
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----
    def start(self) -> None:
        with self._guard:
            self._started = True
            self._wake()

    def _wake(self) -> None:
        if self._started and not self._stopped and self._thread is None and not self._q.empty():
            self._thread = threading.Thread(target=self._worker, name="inspection", daemon=True)
            self._thread.start()

    def stop(self, *, close_store: bool = False) -> None:
        with self._guard:
            self._stopped = True
            self._close_store = self._close_store or close_store
            if self._close_store and self._thread is None:
                self.store.close()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=3)

    # ---- 提交 ----
    def submit(self, scenario_id: str, images: list[bytes]) -> dict[str, Any]:
        sc = self.scenarios.get(scenario_id)
        if sc is None:
            raise KeyError(f"场景不存在:{scenario_id}")
        if not sc.enabled:
            raise PermissionError(f"场景「{sc.name}」未启用:默认只启用有评测数据支撑的组合,请在配置中开启")
        if not images:
            raise ValueError("没有图片")
        if len(images) > MAX_IMAGES:
            raise ValueError(f"一次最多 {MAX_IMAGES} 张,收到 {len(images)} 张")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec = {"id": f"INSP-{now.replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:4].upper()}",
               "scenario": scenario_id, "scenario_name": sc.name, "status": "pending",
               "verdict": None, "confidence": None, "reason": "", "images": [], "created": now}
        task_dir = self.images_root / rec["id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        for i, data in enumerate(images):
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                raise ValueError(f"第 {i + 1} 个文件不是可解码的图片")
            name = f"{i:02d}.jpg"
            cv2.imwrite(str(task_dir / name), arr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            rec["images"].append(name)
        with self._guard:
            if self._stopped:
                raise ValueError("拍照即检正在停止")
            self.store.save(rec)
            self._q.put({"id": rec["id"], "scenario": sc})
            self._wake()
        return rec

    # ---- 判定 ----
    def _worker(self) -> None:
        while True:
            with self._guard:
                try:
                    job = self._q.get_nowait()
                except queue.Empty:
                    if self._close_store:
                        self.store.close()
                    self._thread = None
                    return
            rec = self.store.get(job["id"])
            if rec is None:  # 库记录被清掉:任务作废,不入死循环
                continue
            rec["status"] = "running"
            self.store.save(rec)
            try:
                verdict, conf, reason = self._judge(job["scenario"], self.images_root / rec["id"], rec["images"])
                rec.update(status="done", verdict=verdict, confidence=conf, reason=reason)
            except Exception as err:  # noqa: BLE001 —— 判定失败是任务结果,不是服务崩溃
                rec.update(status="failed", reason=f"判定失败: {err}")
            self.store.save(rec)
            self.publish({"type": "event", "kind": "inspection", "data": {k: rec[k] for k in
                         ("id", "scenario", "status", "verdict", "confidence", "reason")}})

    def _judge(self, sc: Scenario, task_dir: Path, image_names: list[str]) -> tuple[str | None, float | None, str]:
        def enc(name: str) -> str:
            return base64_of(task_dir / name)

        imgs = [enc(n) for n in image_names]
        if sc.mode == "first_article":
            groups = []
            for refs in (sc.ok_refs, sc.ng_refs):
                paths = resolve_refs(refs, self.refs_root)
                if not 1 <= len(paths) <= 3 or any(not resolve_refs((ref,), self.refs_root) for ref in refs):
                    raise RuntimeError("首件OK/NG参考图缺失或数量无效，每组需1到3张有效参考图")
                groups.append([base64_of(p) for p in paths])
            ok_imgs, ng_imgs = groups
            # 多张待检图时逐张判定(首件场景通常一次一张;多张就串行,保证每张都有完整参考上下文)
            verdicts = []
            for img in imgs:
                # Each view needs its own difference evidence. A failed first step is
                # not evidence of "no differences", and must not become a PASS.
                step1 = self.provider.inspect(ok_imgs + [img], DIFF_PROMPT.format(n_ok=len(ok_imgs)))
                raw = step1.get("raw")
                parsed = extract_json(raw) if step1.get("ok") and isinstance(raw, str) else None
                differences = parsed.get("differences") if parsed else None
                if not isinstance(differences, list) or any(not isinstance(v, str) for v in differences):
                    raise RuntimeError("首件差异分析失败，需重新检查或人工复核")
                diffs = json.dumps(differences, ensure_ascii=False)
                step2 = self.provider.inspect(
                    ok_imgs + ng_imgs + [img], VERDICT_PROMPT.format(
                        n_ok=len(ok_imgs), n_ng=len(ng_imgs), differences=diffs))
                if not step2["ok"]:
                    raise RuntimeError(step2["reason"])
                verdicts.append(step2)
            from .judgements import aggregate_judgements
            return aggregate_judgements(verdicts)
        verdicts = []
        for img in imgs:
            out = self.provider.inspect([img], sc.prompt)
            if not out["ok"]:
                raise RuntimeError(out["reason"])
            verdicts.append(out)
        # 多图取最坏结果:任何一张 NG 即 NG,全 PASS 才 PASS(违规判定的保守方向)
        from .judgements import aggregate_judgements
        return aggregate_judgements(verdicts)

    def task_view(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = dict(rec)
        out["image_urls"] = [f"/api/inspection/tasks/{rec['id']}/images/{n}" for n in rec.get("images", [])]
        return out


def base64_of(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode()


def resolve_refs(refs: tuple[str, ...], root: Path) -> list[Path]:
    """参考图条目可以是目录(取其中 jpg)或单文件路径,相对路径基于 refs_root。"""
    out: list[Path] = []
    for r in refs:
        p = Path(r) if Path(r).is_absolute() else root / r
        if p.is_dir():
            out.extend(sorted(p.glob("*.jpg")))
        elif p.is_file():
            out.append(p)
    return out
