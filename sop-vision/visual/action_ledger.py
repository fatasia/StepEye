"""动作影子旁路对账账本：记录影子结果与步骤期望动作的一致性。

只记录、只提示，不驱动规则引擎；一致率供人工决定是否把动作证据升级进 SOP。
同一影子窗口只记一条；未知/低置信度结果不入账（保持未知语义）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

MAX_ROWS = 50000


class ActionLedger:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS action_reconciliation (
            id TEXT PRIMARY KEY, station TEXT, cycle_id TEXT, step_ids TEXT,
            expected TEXT, observed TEXT, confidence REAL, agree INTEGER,
            model_sha256 TEXT, window_id INTEGER, at TEXT, UNIQUE(station, window_id))""")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def record(self, station: str, cycle_id: str | None, step_ids: list[str],
               expected: list[str], observed: str, confidence: float,
               model_sha256: str, window_id: int) -> dict | None:
        """记一条对账；expected 为空表示该步骤未配置期望动作（agree=NULL）。"""
        if confidence < 0 or confidence > 1 or not observed or len(observed) > 100:
            raise ValueError("对账观测值无效")
        agree = None if not expected else int(observed in expected)
        row = {"id": uuid.uuid4().hex, "station": station, "cycle_id": cycle_id,
               "step_ids": json.dumps(step_ids, ensure_ascii=False),
               "expected": json.dumps(expected, ensure_ascii=False), "observed": observed,
               "confidence": float(confidence), "agree": agree,
               "model_sha256": model_sha256, "window_id": int(window_id),
               "at": datetime.now(timezone.utc).isoformat()}
        row_return = {**row, "agree": None if agree is None else bool(agree),
                      "step_ids": step_ids, "expected": list(expected)}
        with self.lock:
            before = self.db.total_changes
            try:
                self.db.execute("BEGIN IMMEDIATE")
                count = self.db.execute("SELECT COUNT(*) FROM action_reconciliation").fetchone()[0]
                if count >= MAX_ROWS:
                    self.db.execute("DELETE FROM action_reconciliation WHERE rowid IN "
                                    "(SELECT rowid FROM action_reconciliation ORDER BY rowid LIMIT ?)",
                                    (count - MAX_ROWS + 1000,))
                self.db.execute("INSERT OR IGNORE INTO action_reconciliation VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (row["id"], station, cycle_id, row["step_ids"], row["expected"],
                                 observed, confidence, agree, model_sha256, int(window_id), row["at"]))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            inserted = self.db.total_changes > before
        # INSERT OR IGNORE 吞掉重复窗口：只有首次插入才算新记录。
        return row_return if inserted else None

    def list(self, station=None, step=None, limit=100):
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM action_reconciliation WHERE (? IS NULL OR station=?) ORDER BY rowid DESC LIMIT ?"
        with self.lock:
            rows = self.db.execute(sql, (station, station, limit)).fetchall()
        items = [self._row(r) for r in rows]
        if step is not None:
            items = [x for x in items if step in x["step_ids"]]
        return items

    def summary(self, station=None):
        sql = """SELECT station,
                   SUM(agree IS NOT NULL) AS judged,
                   SUM(agree = 1) AS agree_count,
                   SUM(agree = 0) AS disagree_count,
                   COUNT(*) AS records
               FROM action_reconciliation WHERE (? IS NULL OR station=?)
               GROUP BY station ORDER BY station"""
        with self.lock:
            rows = self.db.execute(sql, (station, station)).fetchall()
        return [{"station": r[0], "judged": r[1], "agreed": r[2], "disagreed": r[3],
                 "unmapped": r[4] - r[1],
                 "agreement_rate": round(r[2] / r[1], 4) if r[1] else None}
                for r in rows]

    @staticmethod
    def _row(row):
        return {"id": row[0], "station": row[1], "cycle_id": row[2],
                "step_ids": json.loads(row[3]), "expected": json.loads(row[4]),
                "observed": row[5], "confidence": row[6],
                "agree": None if row[7] is None else bool(row[7]),
                "model_sha256": row[8], "window_id": row[9], "at": row[10]}
