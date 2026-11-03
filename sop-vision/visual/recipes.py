"""Immutable business recipes; device/source/model paths stay station-owned."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .dsl import StationConfig, Program

FIELDS = frozenset({"zones", "roi_states", "cycle", "steps", "violations", "safety", "quality"})


class RecipeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, protected_namespaces=())
    name: str = Field(min_length=1, max_length=120)
    model_key: str = Field(min_length=1, max_length=120)
    station_id: str = Field(min_length=1, max_length=160)
    patch: dict = Field(default_factory=dict)


class VersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, protected_namespaces=())
    patch: dict = Field(default_factory=dict)
    snapshot_current: bool = False


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=100)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validated(raw):
    # Compile catches invalid step ordering that schema validation alone cannot catch.
    cfg = StationConfig.model_validate(raw)
    known = {zone.id for zone in cfg.zones}
    if any(rule.zone not in known for rule in cfg.safety.rules):
        raise ValueError("安全规则引用了未定义区域")
    Program.from_config(cfg)
    return cfg.model_dump(mode="json")


def _atomic_write(path, data):
    temp = path.with_name(path.name + ".recipe-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class RecipeStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS recipes (id TEXT PRIMARY KEY, body TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS recipe_versions (id TEXT PRIMARY KEY, recipe TEXT, version INTEGER, body TEXT, UNIQUE(recipe,version))")
        self.db.execute("CREATE TABLE IF NOT EXISTS recipe_switches (id INTEGER PRIMARY KEY AUTOINCREMENT, station TEXT, body TEXT)")
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def list(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM recipes ORDER BY rowid DESC")]

    def get(self, rid):
        with self.lock:
            row = self.db.execute("SELECT body FROM recipes WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError("配方不存在")
        return json.loads(row[0])

    def versions(self, rid):
        self.get(rid)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM recipe_versions WHERE recipe=? ORDER BY version DESC", (rid,))]

    def version(self, vid):
        with self.lock:
            row = self.db.execute("SELECT body FROM recipe_versions WHERE id=?", (vid,)).fetchone()
        if not row:
            raise KeyError("配方版本不存在")
        return json.loads(row[0])

    def save(self, recipe, fields, actor, new=False):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                number = self.db.execute("SELECT COALESCE(MAX(version),0)+1 FROM recipe_versions WHERE recipe=?", (recipe["id"],)).fetchone()[0]
                record = {"id": uuid.uuid4().hex, "recipe_id": recipe["id"], "version": number,
                          "station_id": recipe["station_id"], "model_key": recipe["model_key"],
                          "fields": fields, "created": _now(), "actor": actor}
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
                if new:
                    self.db.execute("INSERT INTO recipes VALUES(?,?)", (recipe["id"], json.dumps(recipe)))
                self.db.execute("INSERT INTO recipe_versions VALUES(?,?,?,?)", (record["id"], recipe["id"], number, encoded))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return record

    def record_switch(self, sid, result):
        with self.lock:
            self.db.execute("INSERT INTO recipe_switches(station,body) VALUES(?,?)", (sid, json.dumps(result)))
            self.db.commit()

    def history(self, sid):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM recipe_switches WHERE station=? ORDER BY id DESC LIMIT 100", (sid,))]


class RecipeService:
    def __init__(self, store: RecipeStore, manager):
        self.store, self.manager = store, manager
        self._switch_lock = threading.Lock()

    def _config(self, sid):
        if sid not in self.manager.paths:
            raise KeyError("工位不存在")
        path = Path(self.manager.paths[sid])
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw.get("station") != sid:
            raise ValueError("工位配置 id 不匹配")
        return path, raw

    def _fields(self, sid, patch, previous=None):
        if set(patch) - FIELDS:
            raise ValueError("配方仅允许区域、SOP、安全规则、ROI 与画质阈值；不允许修改设备或模型路径")
        _, raw = self._config(sid)
        raw.update(previous or {})
        raw.update(patch)
        clean = _validated(raw)
        return {key: clean[key] for key in FIELDS}

    def create(self, body: RecipeBody, actor):
        if not body.name.strip() or not body.model_key.strip():
            raise ValueError("配方名与机型不能为空")
        fields = self._fields(body.station_id, body.patch)
        recipe = {"id": uuid.uuid4().hex, "name": body.name, "model_key": body.model_key,
                  "station_id": body.station_id, "created": _now()}
        version = self.store.save(recipe, fields, actor, new=True)
        return {**recipe, "latest_version": version}

    def add_version(self, rid, body: VersionBody, actor):
        recipe = self.store.get(rid)
        # Explicit snapshots capture current business rules; legacy patches extend the last version.
        prior = None if body.snapshot_current else self.store.versions(rid)[0]["fields"]
        fields = self._fields(recipe["station_id"], body.patch, prior)
        return self.store.save(recipe, fields, actor)

    def status(self, sid):
        path, _ = self._config(sid)
        history = self.store.history(sid)
        latest = next((r for r in history if r["status"] == "applied"), None)
        if history and history[0]["status"] == "failed" and not history[0].get("restored"):
            latest = None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"station_id": sid, "active": latest if latest and latest["config_sha256"] == digest else None,
                "config_sha256": digest, "history": history}

    def switch(self, sid, vid, actor):
        if not self._switch_lock.acquire(blocking=False):
            raise ValueError("配方切换正在进行")
        try:
            version = self.store.version(vid)
            if version["station_id"] != sid:
                raise ValueError("配方不属于此工位")
            path, raw = self._config(sid)
            if self.manager.runtimes[sid].engine.cycle is not None:
                raise ValueError("工位周期进行中，不能切换配方")
            original = path.read_bytes()
            raw.update(version["fields"])
            _validated(raw)
            updated = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")
            result = {"station_id": sid, "version_id": vid, "recipe_id": version["recipe_id"],
                      "model_key": version["model_key"], "actor": actor, "created": _now()}
            _atomic_write(path, updated)
            try:
                self.manager.reload(sid)
            except Exception as exc:
                result.update(status="failed", error=type(exc).__name__)
                try:
                    _atomic_write(path, original)
                    self.manager.reload(sid)
                    result["restored"] = True
                except Exception as restore_error:
                    result.update(restored=False, restore_error=type(restore_error).__name__)
                self.store.record_switch(sid, result)
                raise ValueError("切换失败；" + ("原配置已恢复" if result["restored"] else "恢复失败，工位需人工检查")) from exc
            result.update(status="applied", config_sha256=hashlib.sha256(updated).hexdigest())
            self.store.record_switch(sid, result)
            return result
        finally:
            self._switch_lock.release()
