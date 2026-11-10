"""Bounded operational audit; stores route templates, never request bodies or credentials."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading

from fastapi import APIRouter, Query


class AuditStore:
    def __init__(self, path: Path, limit: int = 10000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._limit = limit
        self.dropped = 0
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=2)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS operations (id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, method TEXT, route TEXT, status INTEGER)")
        self._db.commit()

    def append(self, actor: str, method: str, route: str, status: int):
        # Logging failures must not turn an already-completed business action into an HTTP failure.
        clean = lambda value: ''.join(c for c in str(value) if ord(c) >= 32)[:200]
        with self._lock:
            try:
                self._db.execute("INSERT INTO operations(ts,actor,method,route,status) VALUES (?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), clean(actor), clean(method), clean(route), status))
                self._db.execute("DELETE FROM operations WHERE id <= (SELECT MAX(id) FROM operations)-?", (self._limit,))
                self._db.commit()
            except sqlite3.Error:
                self._db.rollback()
                self.dropped += 1

    def list(self, limit=100, before=None):
        with self._lock:
            rows = self._db.execute("SELECT id,ts,actor,method,route,status FROM operations WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                                    (before, before, limit)).fetchall()
        return {"items": [dict(zip(("id","ts","actor","method","route","status"), r)) for r in rows],
                "dropped": self.dropped, "retained_limit": self._limit}

    def close(self):
        with self._lock:
            self._db.close()


def router(store: AuditStore):
    api = APIRouter()

    @api.get("/api/operations/logs")
    def logs(limit: int = Query(100, ge=1, le=200), before: int | None = Query(None, ge=1)):
        return store.list(limit, before)

    return api
