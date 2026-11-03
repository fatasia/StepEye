"""质量数据导入与不可变分析报告；复用服务的 viewer/operator 鉴权。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Literal
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .msa import analyze_agreement, analyze_grr
from .spc import analyze_spc, defect_pareto

METHODS = {"spc": analyze_spc, "pareto": defect_pareto, "grr": analyze_grr, "agreement": analyze_agreement}
OPTIONS = {"spc": {"lsl", "usl", "baseline_count"}, "grr": {"lsl", "usl"}, "pareto": set(), "agreement": set()}
FIELDS = {"spc": {"value"}, "pareto": {"defect", "count"},
          "grr": {"part", "operator", "trial", "value"},
          "agreement": {"part", "operator", "trial", "label", "reference"}}


class AnalysisBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["spc", "pareto", "grr", "agreement"]
    rows: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=20000)
    csv_text: str | None = Field(default=None, min_length=1, max_length=2*1024*1024)
    options: dict[str, Any] = Field(default_factory=dict)
    title: str = Field(default="", max_length=200)
    unit: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def _one_input(self):
        if (self.rows is None) == (self.csv_text is None):
            raise ValueError("rows与csv_text必须且只能提供一个")
        return self


def import_rows(kind, csv_text):
    if not isinstance(csv_text, str) or len(csv_text.encode("utf-8")) > 2*1024*1024:
        raise ValueError("CSV限2MB UTF-8文本")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV需要不重复的表头")
    if set(reader.fieldnames) - FIELDS[kind]:
        raise ValueError("CSV含未支持的字段")
    result = []
    for row in reader:
        if len(result) >= 20000 or None in row or any(v is None for v in row.values()):
            raise ValueError("CSV超过20000行或列数不一致")
        converted = {k: v.strip() for k,v in row.items()}
        if "value" in converted:
            try: converted["value"] = float(converted["value"])
            except ValueError: raise ValueError("CSV value列必须为数值") from None
        if "count" in converted:
            try:
                raw = converted["count"]
                if not raw.isdigit(): raise ValueError
                converted["count"] = int(raw)
            except ValueError: raise ValueError("CSV count列必须为非负整数") from None
        result.append(converted)
    return result


class QualityReports:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS quality_reports (id TEXT PRIMARY KEY, kind TEXT, created REAL, body TEXT)")
        for operation in ("UPDATE", "DELETE"):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS quality_no_{operation.lower()} BEFORE {operation} ON quality_reports "
                             "BEGIN SELECT RAISE(ABORT,'quality reports are immutable'); END")
        self._db.commit()
        self._closed = False

    def create(self, payload: AnalysisBody | dict, actor: str) -> dict:
        body = payload if isinstance(payload, AnalysisBody) else AnalysisBody.model_validate(payload)
        if set(body.options) - OPTIONS[body.kind]:
            raise ValueError("该分析不支持指定的options字段")
        rows = body.rows if body.rows is not None else import_rows(body.kind, body.csv_text)
        if any(set(row) - FIELDS[body.kind] for row in rows):
            raise ValueError("数据行含未支持字段；请将名称或单位放在title/unit")
        inputs = {"kind": body.kind, "rows": rows, "options": body.options, "title": body.title, "unit": body.unit}
        try:
            canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("导入内容必须为有限JSON数值或文本") from None
        if len(canonical) > 4*1024*1024:
            raise ValueError("分析输入超过4MB")
        result = METHODS[body.kind](rows, **body.options)
        report = {"id": "QA-"+uuid.uuid4().hex, "schema_version": 1, "kind": body.kind,
                  "title": body.title, "unit": body.unit, "created_at": time.time(), "actor": actor,
                  "input_sha256": hashlib.sha256(canonical).hexdigest(), "inputs": inputs, "result": result}
        raw = json.dumps(report, ensure_ascii=False, allow_nan=False)
        with self._guard:
            if self._closed: raise ValueError("质量报告服务已关闭")
            self._db.execute("INSERT INTO quality_reports VALUES (?,?,?,?)", (report["id"], body.kind, report["created_at"], raw))
            self._db.commit()
        return report

    def get(self, report_id):
        with self._guard:
            row = self._db.execute("SELECT body FROM quality_reports WHERE id=?", (report_id,)).fetchone()
        if row is None: raise KeyError("质量报告不存在")
        return json.loads(row[0])

    def list(self, kind="", limit=50, offset=0):
        if kind and kind not in METHODS: raise ValueError("未知分析种类")
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("分页范围无效")
        with self._guard:
            total = self._db.execute("SELECT COUNT(*) FROM quality_reports WHERE (?='' OR kind=?)", (kind,kind)).fetchone()[0]
            rows = self._db.execute("SELECT body FROM quality_reports WHERE (?='' OR kind=?) ORDER BY created DESC,id LIMIT ? OFFSET ?",
                                    (kind,kind,limit,offset)).fetchall()
        items = []
        for (raw,) in rows:
            value = json.loads(raw)
            items.append({k:v for k,v in value.items() if k not in {"inputs", "result"}} | {"status":value["result"]["status"]})
        return {"total":total,"items":items}

    def close(self):
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()


def router(store: QualityReports) -> APIRouter:
    api = APIRouter()

    @api.get("/api/quality/capabilities")
    def capabilities():
        return {"methods": list(METHODS), "max_rows": 20000, "csv_max_bytes": 2*1024*1024,
                "production_approval": False, "input_fields": {k:sorted(v) for k,v in FIELDS.items()},
                "options": {k:sorted(v) for k,v in OPTIONS.items()}}

    @api.post("/api/quality/analyses", status_code=201)
    def create(body: AnalysisBody, request: Request):
        try: return store.create(body, (getattr(request.state,"user",None) or {}).get("username","local"))
        except ValueError as exc: raise HTTPException(400,str(exc)) from None

    @api.get("/api/quality/analyses")
    def listing(kind: str="", limit: int=50, offset: int=0):
        try: return store.list(kind,limit,offset)
        except ValueError as exc: raise HTTPException(400,str(exc)) from None

    def lookup(report_id):
        try: return store.get(report_id)
        except KeyError: raise HTTPException(404,"质量报告不存在") from None

    @api.get("/api/quality/analyses/{report_id}/export")
    def export(report_id: str):
        result = lookup(report_id)
        return Response(json.dumps(result,ensure_ascii=False,allow_nan=False), media_type="application/json",
                        headers={"Content-Disposition":f'attachment; filename="{result["id"]}.json"'})

    @api.get("/api/quality/analyses/{report_id}")
    def detail(report_id: str):
        return lookup(report_id)

    return api
