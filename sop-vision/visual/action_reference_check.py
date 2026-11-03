"""参考卡驱动的动作影子判定：把三帧参考卡与影子分类结果做一致性对账。

边界：动作分类器是公开视频模型（VideoMAE K710），类别与工业动作天然不对齐；
未配置类别映射（step → 影子类别）的一律输出 no_data，不硬套类别。
记录不可变；同一 (工位, 步骤, 周期, 参考版本) 重复评估返回已有记录。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid

VERDICTS = ("match", "deviation", "no_data")


class ReferenceChecks:
    """不可变 SQLite 对账记录；触发器拒绝 UPDATE/DELETE（同 quality_routes 模式）。"""

    def __init__(self, db_path):
        self._guard = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS reference_checks ("
                         "dedupe TEXT PRIMARY KEY, id TEXT, station TEXT, body TEXT)")
        for operation in ("UPDATE", "DELETE"):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS reference_checks_no_{operation.lower()} "
                             f"BEFORE {operation} ON reference_checks "
                             "BEGIN SELECT RAISE(ABORT,'reference checks are immutable'); END")
        self._db.commit()
        self._closed = False

    def close(self) -> None:
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()

    def create(self, record: dict) -> tuple[dict, bool]:
        dedupe = f"{record['station_id']}|{record['step_id']}|{record['cycle_id']}|{record['reference_version']}"
        raw = json.dumps(record, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self._guard:
            if self._closed:
                raise ValueError("参考判定服务已关闭")
            existing = self._db.execute("SELECT body FROM reference_checks WHERE dedupe=?", (dedupe,)).fetchone()
            if existing:
                return json.loads(existing[0]), False
            self._db.execute("INSERT INTO reference_checks VALUES (?,?,?,?)",
                             (dedupe, record["id"], record["station_id"], raw))
            self._db.commit()
        return record, True

    def list(self, station_id: str = "", limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("分页范围无效")
        with self._guard:
            rows = self._db.execute(
                "SELECT body FROM reference_checks WHERE (?='' OR station=?) "
                "ORDER BY rowid DESC LIMIT ?",
                (station_id, station_id, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]


def evaluate(records: list[dict], mapping: dict[str, str],
             observations: list[dict], *, min_conf: float = .6) -> list[dict]:
    """纯函数对账：记录逐 (步骤, 周期) 判定。

    records：ActionReferenceStore.list 的参考卡（每 step 取最新版本）。
    observations：影子结果 + 活跃窗口，行含 step_id/cycle_id/label/confidence；
    label=None 表示该窗口有影子在跑但没有可接受结果（低置信/缺失）。
    判定：映射缺失或无观测 → no_data；有观测且最高置信命中映射类别 → match；否则 deviation。
    """
    if not isinstance(mapping, dict) or any(not isinstance(v, str) or not v.strip() for v in mapping.values()):
        raise ValueError("mapping 必须为 step_id → 非空影子类别的字典")
    latest: dict[str, dict] = {}
    for record in records:
        sid = record.get("step_id")
        if not sid:
            continue
        if sid not in latest or record["version"] > latest[sid]["version"]:
            latest[sid] = record
    seen: dict[tuple[str, str], list[dict]] = {}
    for obs in observations:
        missing = {"step_id", "cycle_id"} - set(obs)
        if missing:
            raise ValueError(f"观测缺少字段：{sorted(missing)}")
        label, conf = obs.get("label"), obs.get("confidence", 0)
        if label is not None and (not isinstance(label, str) or not isinstance(conf, (int, float))):
            raise ValueError("观测 label 须为字符串或 None，confidence 须为数值")
        seen.setdefault((obs["step_id"], str(obs["cycle_id"])), []).append(obs)
    verdicts = []
    for step_id, reference in sorted(latest.items()):
        for (obs_step, cycle_id), rows in sorted(seen.items()):
            if obs_step != step_id:
                continue
            expected = mapping.get(step_id)
            hit = expected is not None and any(
                o.get("label") == expected and float(o.get("confidence", 0)) >= min_conf for o in rows)
            if expected is None:
                verdict = "no_data"
            elif hit:
                verdict = "match"
            else:
                verdict = "deviation"
            verdicts.append({
                "id": "RC-" + uuid.uuid4().hex, "created_at": time.time(),
                "station_id": reference["station_id"], "step_id": step_id,
                "step_name": reference.get("step_name", ""), "cycle_id": cycle_id,
                "reference_id": reference["id"], "reference_version": reference["version"],
                "expected_label": expected, "observed": [
                    {"label": o.get("label"), "confidence": o.get("confidence")} for o in rows],
                "verdict": verdict,
                "scope": "公开视频动作分类器对账；类别映射未配置或无观测时为 no_data，不构成 SOP 合规判定"})
    # 从未出现观测的参考步骤：补一条周期为空的 no_data，让"影子完全没跑"也可对账可见。
    observed_steps = {step for step, _ in seen}
    for step_id, reference in sorted(latest.items()):
        if step_id not in observed_steps:
            verdicts.append({
                "id": "RC-" + uuid.uuid4().hex, "created_at": time.time(),
                "station_id": reference["station_id"], "step_id": step_id,
                "step_name": reference.get("step_name", ""), "cycle_id": "",
                "reference_id": reference["id"], "reference_version": reference["version"],
                "expected_label": mapping.get(step_id), "observed": [], "verdict": "no_data",
                "scope": "公开视频动作分类器对账；类别映射未配置或无观测时为 no_data，不构成 SOP 合规判定"})
    return verdicts
