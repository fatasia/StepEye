"""标注 REST 适配。认证由主服务统一处理；资源定位只接受已落库 ID。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .labels import LabelError, LabelStore
from .label_jobs import LabelEngines, LabelJobs


class LabelProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    classes: list[str] = Field(min_length=1, max_length=100)
    scene_id: str = Field(default="", max_length=100)
    license_ref: str = Field(default="", max_length=500)


class LabelDraftBody(BaseModel):
    expected_revision: int = Field(ge=0)
    targets: list[dict[str, Any]] = Field(max_length=500)


class LabelConfirmBody(BaseModel):
    expected_revision: int = Field(ge=0)
    background: bool = False


class LabelRestoreBody(BaseModel):
    expected_revision: int = Field(ge=0)
    revision: int = Field(ge=0)


class LabelVersionBody(BaseModel):
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    val_ratio: float = Field(default=0.2, ge=0.05, le=0.5, allow_inf_nan=False)


class LabelJobBody(BaseModel):
    project_id: str
    asset_ids: list[str] = Field(min_length=1, max_length=200)
    engine_id: str
    threshold: float = Field(default=0.4, ge=0.05, le=0.95, allow_inf_nan=False)
    idempotency_key: str = Field(min_length=1, max_length=100)


def router(labels: LabelStore, jobs: LabelJobs, engines: LabelEngines) -> APIRouter:
    api = APIRouter()

    def actor(request: Request) -> str:
        return request.state.user["username"]

    @api.get("/api/label-projects")
    def projects(request: Request) -> list[dict]:
        rows = labels.list_projects()
        user = request.state.user or {}
        from .data_scope import scope_ids
        allowed = scope_ids(user)
        return [r for r in rows if allowed is None or r["id"] in allowed]

    @api.post("/api/label-projects", status_code=201)
    def project_add(body: LabelProjectBody) -> dict:
        return labels.create_project(body.name, body.classes, body.scene_id, body.license_ref)

    @api.get("/api/label-projects/{pid}/assets")
    def assets(pid: str, state: str = "", limit: int = Query(100, ge=1, le=200),
               offset: int = Query(0, ge=0)) -> dict:
        return labels.list_assets(pid, state, limit, offset)

    @api.post("/api/label-projects/{pid}/import-dataset", status_code=201)
    async def import_zip(pid: str, request: Request, file: UploadFile = File(...),
                         group_id: str = Form(""), license_ref: str = Form(""), synthetic: bool = Form(False)) -> dict:
        from .label_import import MAX_ARCHIVE_BYTES, import_dataset
        raw = await file.read(MAX_ARCHIVE_BYTES + 1)
        return await asyncio.to_thread(import_dataset, labels, pid, raw, actor(request), group_id, license_ref, synthetic)

    @api.post("/api/label-projects/{pid}/assets", status_code=201)
    async def upload(pid: str, files: list[UploadFile] = File(...), group_id: str = Form(""),
                     illumination_bucket: str = Form("unknown"), license_ref: str = Form(""),
                     synthetic: bool = Form(False)) -> dict:
        project = labels.get_project(pid)
        if not 1 <= len(files) <= 100:
            raise HTTPException(400, "单批上传1–100张图片")
        # 一次读取有界；先校验总量，再写入，防止批量中途超过配额。
        payloads, total = [], 0
        for file in files:
            raw = await file.read(20 * 1024 * 1024 + 1)
            total += len(raw)
            if len(raw) > 20 * 1024 * 1024 or total > 100 * 1024 * 1024:
                raise HTTPException(413, "每张≤20MB，单批≤100MB")
            payloads.append((file.filename or "image", raw))
        metadata = {"group_id": group_id, "illumination_bucket": illumination_bucket,
                    "license_ref": license_ref or project["license_ref"], "synthetic": synthetic, "source_kind": "upload"}
        items, errors = [], []
        for name, raw in payloads:
            try:
                items.append(await asyncio.to_thread(labels.add_asset, pid, raw, name, metadata))
            except LabelError as e:
                errors.append({"name": name[:150], "message": str(e)})
        return {"items": items, "errors": errors, "total": len(items)}

    @api.get("/api/label-assets/{aid}/annotations")
    def annotations(aid: str) -> dict:
        return labels.get_asset(aid)

    @api.put("/api/label-assets/{aid}/annotations")
    def draft(aid: str, body: LabelDraftBody, request: Request) -> dict:
        return labels.save_draft(aid, body.targets, body.expected_revision, actor(request), producer="human")

    @api.put("/api/label-assets/{aid}/agent-draft")
    def agent_draft(aid: str, body: LabelDraftBody, request: Request) -> dict:
        user = request.state.user or {}
        if not user.get("mcp_token_id") or "draft" not in user.get("scopes", []):
            raise HTTPException(403, "需要具备 draft 范围的 MCP 专用令牌")
        return labels.save_draft(aid, body.targets, body.expected_revision, actor(request), producer="agent",
                                 provenance={"mcp_token_id": user["mcp_token_id"]})

    @api.post("/api/label-assets/{aid}/confirm")
    def confirm(aid: str, body: LabelConfirmBody, request: Request) -> dict:
        return labels.confirm(aid, body.expected_revision, actor(request), body.background)

    @api.get("/api/label-assets/{aid}/revisions")
    def revisions(aid: str) -> list[dict]:
        return labels.revisions(aid)

    @api.post("/api/label-assets/{aid}/restore")
    def restore(aid: str, body: LabelRestoreBody, request: Request) -> dict:
        return labels.restore(aid, body.revision, body.expected_revision, actor(request))

    @api.get("/api/label-assets/{aid}/image")
    def image(aid: str) -> FileResponse:
        return FileResponse(labels.image_path(aid), media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @api.get("/api/label-projects/{pid}/quality")
    def quality(pid: str) -> dict:
        return labels.quality(pid)

    @api.get("/api/label-projects/{pid}/versions")
    def versions(pid: str) -> list[dict]:
        return labels.list_versions(pid)

    @api.post("/api/label-projects/{pid}/versions", status_code=201)
    def version(pid: str, body: LabelVersionBody, request: Request) -> dict:
        return labels.create_version(pid, body.seed, body.val_ratio, actor(request))

    @api.get("/api/label-projects/{pid}/versions/{vid}/export.zip")
    def export(pid: str, vid: str) -> FileResponse:
        return FileResponse(labels.export_path(pid, vid), media_type="application/zip", filename=f"dataset-{vid}.zip")

    @api.get("/api/label-engines")
    def available_engines() -> list[dict]:
        return engines.list()

    @api.post("/api/label-jobs", status_code=202)
    def submit(body: LabelJobBody, request: Request) -> dict:
        return jobs.submit(body.project_id, body.asset_ids, body.engine_id, body.threshold, body.idempotency_key, actor(request))

    @api.get("/api/label-jobs")
    def job_list(project_id: str) -> list[dict]:
        return jobs.list(project_id)

    @api.get("/api/label-jobs/{jid}")
    def job_get(jid: str) -> dict:
        return jobs.get(jid)

    @api.post("/api/label-jobs/{jid}/cancel")
    def cancel(jid: str) -> dict:
        return jobs.cancel(jid)

    return api


def check_mcp_read(request: Request, user: dict, labels: LabelStore, jobs: LabelJobs) -> None:
    """明确的 MCP 资源白名单，默认拒绝其余 API，不依赖工具的自报 hint。"""
    import re
    write = re.fullmatch(r"/api/label-assets/([^/]+)/agent-draft", request.url.path)
    if request.method == "PUT" and write and "draft" in user["scopes"]:
        pid = labels.get_asset(write[1])["project_id"]
        if not user["project_ids"] or pid in user["project_ids"]:
            return
    if request.method != "GET" or "read" not in user["scopes"]:
        raise HTTPException(403, "MCP令牌不允许该操作")
    path, project_id = request.url.path, None
    if path in ("/api/label-projects", "/api/mcp/scenes"):
        return  # 端点内按 project_ids 过滤
    match = re.fullmatch(r"/api/label-projects/([^/]+)/(assets|quality|versions)", path)
    if match:
        project_id = match[1]
    match = re.fullmatch(r"/api/label-assets/([^/]+)/(annotations|image)", path)
    if match:
        if match[2] == "image" and "images" not in user["scopes"]:
            raise HTTPException(403, "令牌未获图像读取授权")
        project_id = labels.get_asset(match[1])["project_id"]
    if path == "/api/label-jobs":
        project_id = request.query_params.get("project_id")
    match = re.fullmatch(r"/api/label-jobs/([^/]+)", path)
    if match:
        project_id = jobs.get(match[1])["project_id"]
    if not project_id:
        raise HTTPException(403, "该资源不在MCP只读范围")
    if user["project_ids"] and project_id not in user["project_ids"]:
        raise HTTPException(403, "该项目不在令牌授权范围")
