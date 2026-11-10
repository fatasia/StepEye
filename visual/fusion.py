"""设备信号入箱：SOP 按帧消费；温场观察器在验收入站后独立处理。

接收序号与审计落 SQLite，重启不重放已接受信号。队列是有界内存队列，
202 只表示已接收，不等于设备动作已执行；进程中断的未消费项记为 abandoned。
"""
from __future__ import annotations

import hashlib
import json
import math
import logging
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .events import SIGNAL, Event

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$"


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("信号值嵌套超过 16 层")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("信号值必须是有限 JSON 值")


class SignalPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(pattern=IDENTIFIER)
    name: str = Field(pattern=IDENTIFIER)
    sequence: int = Field(ge=0, le=2**63 - 1, strict=True)
    observed_at: float = Field(gt=0, allow_inf_nan=False, strict=True)
    ttl_ms: int = Field(default=5000, ge=1, le=600_000, strict=True)
    quality: Literal["ok", "uncertain", "invalid"] = "ok"
    value: Any = None

    @field_validator("value")
    @classmethod
    def check_value(cls, value):
        _json_value(value)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 16 * 1024:
            raise ValueError("信号值超过 16KB")
        return value


class SignalError(Exception):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status, self.code = status, code


class SignalAuditStore:
    """最近序号、内容指纹与收发审计；不保存信号原始值到审计日志。"""
    def __init__(self, db_path: Path, audit_limit: int = 50_000):
        if audit_limit < 100:
            raise ValueError("audit_limit 至少为 100")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS signal_heads (scope TEXT, source TEXT, name TEXT,"
                         " sequence INTEGER, fingerprint TEXT, PRIMARY KEY(scope,source,name))")
        self._db.execute("CREATE TABLE IF NOT EXISTS signal_audit (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                         " scope TEXT, source TEXT, name TEXT, sequence INTEGER, fingerprint TEXT,"
                         " observed_at REAL, received_at REAL, expires_at REAL, quality TEXT, state TEXT,"
                         " reason TEXT, finished_at REAL, engine_ts REAL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_signal_audit_seq ON signal_audit(scope,source,name,sequence)")
        self._db.commit()
        self.audit_limit = audit_limit
        self._writes = 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _insert(self, scope: str, packet: SignalPacket, fingerprint: str, received_at: float,
                expires_at: float, state: str, reason: str) -> int:
        cur = self._db.execute("INSERT INTO signal_audit(scope,source,name,sequence,fingerprint,observed_at,"
                               "received_at,expires_at,quality,state,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (scope, packet.source, packet.name, packet.sequence, fingerprint, packet.observed_at,
                                received_at, expires_at, packet.quality, state, reason))
        self._writes += 1
        if self._writes % 128 == 0:
            # 只清理已终结记录；排队项与最近序号不因审计保留上限被丢掉。
            self._db.execute("DELETE FROM signal_audit WHERE id IN (SELECT id FROM signal_audit "
                             "WHERE state!='queued' ORDER BY id DESC LIMIT -1 OFFSET ?)", (self.audit_limit,))
        return cur.lastrowid

    def admit(self, scope: str, packet: SignalPacket, fingerprint: str, received_at: float,
              has_capacity: bool, max_future_skew_s: float, max_keys: int = 256,
              legacy: bool = False) -> dict[str, Any]:
        expires_at = packet.observed_at + packet.ttl_ms / 1000
        error = None
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                head = self._db.execute("SELECT sequence,fingerprint FROM signal_heads WHERE scope=? AND source=? AND name=?",
                                        (scope, packet.source, packet.name)).fetchone()
                if legacy:
                    # 序号在同一个 SQLite 写事务内分配与提交，跨线程/重启不回退。
                    next_sequence = head[0] + 1 if head else 1
                    if next_sequence > 2**63 - 1:
                        raise SignalError("兼容入口序号耗尽", 409, "sequence_exhausted")
                    packet = packet.model_copy(update={"sequence": next_sequence})
                    canonical = json.dumps(packet.model_dump(), ensure_ascii=False, sort_keys=True,
                                           allow_nan=False, separators=(",", ":"))
                    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                prior = self._db.execute("SELECT id,fingerprint FROM signal_audit WHERE scope=? AND source=? AND name=? "
                                         "AND sequence=? AND state IN ('queued','consumed','expired','abandoned') "
                                         "ORDER BY id LIMIT 1", (scope, packet.source, packet.name, packet.sequence)).fetchone()
                old_fingerprint = prior[1] if prior else (head[1] if head and head[0] == packet.sequence else None)
                if old_fingerprint == fingerprint:
                    state, reason = "duplicate", "duplicate"
                else:
                    if old_fingerprint is not None:
                        error = SignalError("相同序号的信号内容冲突", 409, "sequence_conflict")
                    elif head and packet.sequence < head[0]:
                        error = SignalError("信号序号落后于已接受序号", 409, "out_of_order")
                    elif packet.observed_at - received_at > max_future_skew_s:
                        error = SignalError("设备时钟超前超过允许偏差，请校时", 422, "future_clock")
                    elif expires_at <= received_at:
                        error = SignalError("信号已过期", 410, "expired")
                    elif head is None and self._db.execute("SELECT COUNT(*) FROM signal_heads WHERE scope=?", (scope,)).fetchone()[0] >= max_keys:
                        error = SignalError("工位信号来源/名称数量已达上限", 429, "source_limit")
                    elif not has_capacity:
                        error = SignalError("信号队列已满，请退避后重试", 429, "queue_full")
                    state, reason = ("rejected", error.code) if error else ("queued", "accepted_legacy" if legacy else "accepted")
                audit_id = self._insert(scope, packet, fingerprint, received_at, expires_at, state, reason)
                if state == "queued":
                    self._db.execute("INSERT INTO signal_heads VALUES (?,?,?,?,?) ON CONFLICT(scope,source,name) "
                                     "DO UPDATE SET sequence=excluded.sequence,fingerprint=excluded.fingerprint",
                                     (scope, packet.source, packet.name, packet.sequence, fingerprint))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
        if error:
            raise error
        return {"status": "accepted" if state == "queued" else "duplicate", "audit_id": audit_id,
                "sequence": packet.sequence, "received_at": received_at, "expires_at": expires_at}

    def finish(self, audit_id: int, state: Literal["consumed", "expired", "abandoned"],
               at: float, engine_ts: float | None = None, reason: str = "") -> None:
        with self._lock:
            self._db.execute("UPDATE signal_audit SET state=?,reason=?,finished_at=?,engine_ts=? "
                             "WHERE id=? AND state='queued'", (state, reason or state, at, engine_ts, audit_id))
            self._db.commit()

    def recover_abandoned(self, at: float | None = None) -> int:
        """仅在独占进程启动时调用；不恢复队列，不把旧信号重新交给引擎。"""
        with self._lock:
            cur = self._db.execute("UPDATE signal_audit SET state='abandoned',reason='process_restart',finished_at=? "
                                   "WHERE state='queued'", (time.time() if at is None else at,))
            self._db.commit()
        return cur.rowcount

    def audit(self, scope: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit 需在 1..500")
        with self._lock:
            rows = self._db.execute("SELECT id,source,name,sequence,fingerprint,observed_at,received_at,expires_at,"
                                    "quality,state,reason,finished_at,engine_ts FROM signal_audit WHERE scope=? "
                                    "ORDER BY id DESC LIMIT ?", (scope, limit)).fetchall()
        keys = ("id", "source", "name", "sequence", "fingerprint", "observed_at", "received_at", "expires_at",
                "quality", "state", "reason", "finished_at", "engine_ts")
        return [dict(zip(keys, row)) for row in rows]


class SignalInbox:
    def __init__(self, store: SignalAuditStore, scope: str, capacity: int = 128,
                 max_future_skew_s: float = 2.0, clock=time.time, max_keys: int = 256):
        if capacity < 1 or max_keys < 1 or not math.isfinite(max_future_skew_s) or max_future_skew_s < 0:
            raise ValueError("capacity 至少为 1，时钟容差须为有限非负数")
        self.store, self.scope, self.capacity = store, scope, capacity
        self.max_future_skew_s, self.clock = max_future_skew_s, clock
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._queue: deque[tuple[SignalPacket, dict[str, Any]]] = deque()
        self._consumer: threading.Thread | None = None
        self._closed = False
        self._observer = None
        self._observer_keys = frozenset()

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def push(self, packet: SignalPacket | dict) -> dict[str, Any]:
        return self._push(packet)

    def push_legacy(self, name: str, value: Any, ttl_ms: int = 2000) -> dict[str, Any]:
        """name/value 旧接口兼容：接收时刻、服务端持久序号，不能冒充设备来源。

        旧接口没有客户端序号，无法识别跨请求重试；需幂等保证的设备应迁移严格入口。
        """
        return self._push({"source": "legacy-http", "name": name, "sequence": 0,
                           "observed_at": self.clock(), "ttl_ms": ttl_ms, "quality": "ok", "value": value}, legacy=True)

    def _push(self, packet: SignalPacket | dict, legacy: bool = False) -> dict[str, Any]:
        try:
            packet = SignalPacket.model_validate(packet)
            canonical = json.dumps(packet.model_dump(), ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
            # 深复制 value，调用方之后修改字典不会改变已验收的信号或其指纹。
            packet = SignalPacket.model_validate_json(canonical)
        except (ValidationError, ValueError, TypeError):
            raise SignalError("信号字段无效，请检查来源、序号、采集时间、TTL 和 JSON 值", 422, "schema") from None
        if packet.source == "legacy-http" and not legacy:
            raise SignalError("legacy-http 是旧接口保留来源", 422, "reserved_source")
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            if self._closed:
                raise SignalError("工位信号队列已关闭", 503, "closed")
            observer = self._observer if (packet.source, packet.name) in self._observer_keys else None
            has_capacity = len(self._queue) < self.capacity
            receipt = self.store.admit(self.scope, packet, fingerprint, self.clock(),
                                       has_capacity or observer is not None, self.max_future_skew_s,
                                       max_keys=self.max_keys, legacy=legacy)
            receipt["queued_for_sop"] = receipt["status"] == "accepted" and has_capacity
            if receipt["status"] == "accepted":
                if legacy:
                    packet = packet.model_copy(update={"sequence": receipt["sequence"]})
                if has_capacity:
                    self._queue.append((packet, receipt))
                else:
                    self.store.finish(receipt["audit_id"], "consumed", self.clock(),
                                      reason="thermal_observer_only")
            result = {**receipt, "queued": len(self._queue)}
        # Never hold the inbox lock during observers/listeners; SOP owns its queue unchanged.
        if observer is not None and receipt["status"] == "accepted":
            try:
                observer(packet.model_copy(deep=True), dict(result))
                result["observer_status"] = "processed"
            except Exception:
                logging.getLogger(__name__).exception("Signal observer failed: %s", self.scope)
                result["observer_status"] = "failed"
        return result

    def bind_observer(self, observer, keys) -> None:
        """Bind one lightweight thermal observer and its explicit source/name allowlist."""
        with self._lock:
            if self._closed:
                raise RuntimeError("信号队列已关闭")
            if self._observer is not None:
                raise RuntimeError("信号观察器已绑定")
            self._observer = observer
            self._observer_keys = frozenset(keys)

    def bind_consumer(self) -> None:
        """由采集线程 run() 开头调用；HTTP 工作线程不得绑定/消费。"""
        current = threading.current_thread()
        with self._lock:
            if self._closed:
                raise RuntimeError("信号队列已关闭")
            if self._consumer is not None and self._consumer is not current:
                raise RuntimeError("信号队列已经绑定其他采集线程")
            self._consumer = current

    def drain(self, frame_ts: float) -> list[Event]:
        if not math.isfinite(frame_ts) or frame_ts < 0:
            raise ValueError("帧时间必须为有限非负相对时间")
        with self._lock:
            if self._consumer is not threading.current_thread():
                raise RuntimeError("仅绑定的采集线程可以消费信号队列")
            if self._closed:
                return []
            events = []
            while self._queue:
                packet, receipt = self._queue.popleft()
                now = self.clock()
                if receipt["expires_at"] <= now:
                    self.store.finish(receipt["audit_id"], "expired", now, frame_ts, "expired_before_consume")
                    continue
                provenance = {"source": packet.source, "sequence": packet.sequence,
                              "observed_at": packet.observed_at, "received_at": receipt["received_at"],
                              "consumed_at": now, "expires_at": receipt["expires_at"],
                              "valid_until_ts": frame_ts + receipt["expires_at"] - now,
                              "quality": packet.quality, "audit_id": receipt["audit_id"]}
                if packet.source == "legacy-http":
                    provenance["legacy"] = True
                events.append(Event(ts=frame_ts, type=SIGNAL, name=packet.name, value=packet.value,
                                    source=packet.source, conf=1.0 if packet.quality == "ok" else 0.0,
                                    data=provenance))
                self.store.finish(receipt["audit_id"], "consumed", now, frame_ts)
            return events

    def close(self) -> None:
        with self._lock:
            self._closed = True
            while self._queue:
                _, receipt = self._queue.popleft()
                self.store.finish(receipt["audit_id"], "abandoned", self.clock(), reason="station_stopped")
