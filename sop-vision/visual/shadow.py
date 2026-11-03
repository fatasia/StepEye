"""影子模式(shadow mode):只记录不动作,统计漏检率/误报率 —— 上线前验收的强制流程。

与在线模式的区别:检测判定照常跑,但周期结果不进 PASS/FAIL 正式统计,全部进
shadow_samples 表,等人工真值标注后算准确率。这解决「仿真结果不能外推」的验收红线:
真值标注完成前,系统不给客户报任何准确率数字。

用法:
  运行时: 工位 YAML 加 source.shadow: true(或 serve --shadow 全局)
  统计:   python -m visual shadow-report --data data [--since 2026-11-01]
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class ShadowStore:
    """影子样本:每次周期结束/检测结论都存一条,带模型版本与规则指纹,供人工标注。"""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS shadow_samples (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " kind TEXT, station TEXT, ref_id TEXT, system_verdict TEXT, confidence REAL,"
            " truth TEXT, created TEXT, body TEXT)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_shadow_truth ON shadow_samples(kind, truth)")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, kind: str, station: str, ref_id: str, system_verdict: str,
            confidence: float | None, body: dict[str, Any]) -> int:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO shadow_samples (kind, station, ref_id, system_verdict, confidence, truth, created, body)"
                " VALUES (?,?,?,?,?,NULL,?,?)",
                (kind, station, ref_id, "NG" if system_verdict == "FAIL" else system_verdict,
                 confidence, now, json.dumps(body, ensure_ascii=False)))
            self._db.commit()
        return cur.lastrowid

    def set_truth(self, sample_id: int, truth: str) -> bool:
        if truth not in ("PASS", "NG"):
            raise ValueError("truth 必须是 PASS 或 NG")
        with self._lock:
            cur = self._db.execute("UPDATE shadow_samples SET truth=? WHERE id=?", (truth, sample_id))
            self._db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _sample(row) -> dict[str, Any]:
        d = dict(zip(("id", "kind", "station", "ref_id", "system_verdict", "confidence", "truth", "created", "body"), row))
        d["body"] = json.loads(d["body"] or "{}")
        if d["system_verdict"] == "FAIL":
            d["system_verdict"] = "NG"
        return d

    def get(self, sample_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT id,kind,station,ref_id,system_verdict,confidence,truth,created,body "
                                   "FROM shadow_samples WHERE id=?", (sample_id,)).fetchone()
        return self._sample(row) if row else None

    def list(self, unlabeled: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT id,kind,station,ref_id,system_verdict,confidence,truth,created,body "
                                    "FROM shadow_samples" + (" WHERE truth IS NULL" if unlabeled else "") +
                                    " ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [self._sample(r) for r in rows]

    def report(self, kind: str | None = None, since: str | None = None) -> dict[str, Any]:
        """按真值算混淆:漏检(system=PASS truth=NG)、误杀(system=NG truth=PASS)、待标注数。"""
        q = "SELECT kind, system_verdict, truth, COUNT(*) FROM shadow_samples WHERE 1=1"
        args: list = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if since:
            q += " AND created>=?"
            args.append(since)
        q += " GROUP BY kind, system_verdict, truth"
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        kinds: dict[str, Any] = {}
        for k, sys_v, truth, n in rows:
            d = kinds.setdefault(k, {"labeled": 0, "unlabeled": 0, "miss": 0, "false_alarm": 0,
                                     "correct": 0, "evaluated": 0, "uncertain": 0, "agree_rate": None})
            if truth is None:
                d["unlabeled"] += n
                continue
            d["labeled"] += n
            sys_v = "NG" if sys_v == "FAIL" else sys_v
            if sys_v not in ("PASS", "NG"):
                d["uncertain"] += n
                continue
            d["evaluated"] += n
            if sys_v == truth:
                d["correct"] += n
            elif truth == "NG":
                d["miss"] += n  # 漏检:EHS 最危险方向
            elif truth == "PASS":
                d["false_alarm"] += n  # 误杀
        for d in kinds.values():
            if d["evaluated"]:
                d["agree_rate"] = round(d["correct"] / d["evaluated"], 3)
        return {"kinds": kinds, "generated": datetime.now().isoformat(timespec="seconds")}


def start_shadow_recorder(runtime, shadow: "ShadowStore") -> None:
    """挂到工位 runtime:周期结束即影子采样(不影响在线判定与展示)。"""
    def on_event(kind: str, p: dict[str, Any]) -> None:
        if kind != "cycle_end":
            return
        shadow.add("sop_cycle", runtime.cfg.station, p.get("cycle_id", ""), p.get("result", ""),
                   None, {"result": p.get("result"), "violations": p.get("violations"),
                          "rules_hash": p.get("rules_hash")})
    runtime.listeners.append(on_event)
