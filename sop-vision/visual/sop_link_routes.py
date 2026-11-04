"""SOP 文档-步骤关联 API：草稿生成/编辑/确认/影响提示，不触碰运行中的工位规则。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import yaml

from .documents import DocumentError
from .sop_links import SopLinkError, document_paragraph_snapshot


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=100)
    station_id: str = Field(min_length=1, max_length=160)
    paragraphs: list[int] | None = None
    template_id: str | None = Field(default=None, max_length=100)


class StepsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[dict]


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: str | None = Field(default=None, max_length=160)


def _zones_of(manager, station_id: str) -> list[str]:
    """从工位 YAML 读取真实区域 id；工位不存在时给出可操作错误。"""
    path = manager.paths.get(station_id)
    if path is None:
        raise SopLinkError(f"工位不存在：{station_id}", 404)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    zones = [z.get("id") for z in raw.get("zones", []) if isinstance(z, dict) and z.get("id")]
    if not zones:
        raise SopLinkError(f"工位 {station_id} 未定义任何区域，无法确认步骤", 422)
    return zones


def router(store, documents, manager):
    api = APIRouter()

    def user(request, write=False):
        value = getattr(request.state, "user", None)
        if not isinstance(value, dict):
            raise HTTPException(401, "请登录后使用 SOP 关联")
        role = value.get("role")
        try:
            from .documents import authorize
            authorize(role, write=write)
        except DocumentError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return value

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SopLinkError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except DocumentError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from None

    @api.post("/api/sop/links/drafts", status_code=201)
    def create_draft(body: DraftBody, request: Request):
        actor = user(request, write=True)
        role = actor["role"]
        version = call(documents.get_version, body.version_id, role)
        paragraphs = call(document_paragraph_snapshot, documents, body.version_id, role,
                          body.paragraphs)
        return call(store.create, body.version_id, version["version"], version["document_id"],
                    body.station_id, paragraphs, role, actor["username"], body.template_id)

    @api.get("/api/sop/links/drafts")
    def listing(request: Request, document_id: str | None = None,
                station_id: str | None = None):
        role = user(request)["role"]
        return {"items": call(store.list, role, document_id, station_id)}

    @api.put("/api/sop/links/drafts/{did}")
    def update(did: str, body: StepsBody, request: Request):
        actor = user(request, write=True)
        return call(store.update_steps, did, body.steps, actor["role"], actor["username"])

    @api.post("/api/sop/links/drafts/{did}/confirm")
    def confirm(did: str, body: ConfirmBody, request: Request):
        actor = user(request, write=True)
        target = call(store.get, did, actor["role"])
        station_id = body.station_id or target["station_id"]
        zones = call(_zones_of, manager, station_id)
        return call(store.confirm, did, actor["role"], actor["username"], zones)

    @api.post("/api/sop/links/drafts/{did}/discard")
    def discard(did: str, request: Request):
        actor = user(request, write=True)
        return call(store.discard, did, actor["role"])

    @api.get("/api/sop/links/drafts/{did}/recipe-patch")
    def patch(did: str, request: Request):
        role = user(request, write=True)["role"]
        return call(store.recipe_patch, did, role)

    @api.get("/api/sop/links/impact")
    def impact(request: Request, document_id: str):
        role = user(request)["role"]
        versions = call(documents.list, role, document_id)
        if not versions:
            raise HTTPException(404, "文档不存在")
        return call(store.impact, role, document_id, versions,
                    lambda vid: call(documents.paragraphs, vid, role))

    return api
