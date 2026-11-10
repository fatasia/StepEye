"""模型发布路由:candidate 创建(operator)、审批/激活/回滚(admin)、读取(viewer)。

server.py 接线(主会话执行,此处不改 server.py):

    from .model_release import ModelReleases
    from .model_release_routes import router as model_release_router
    app.include_router(model_release_router(
        ModelReleases(data_dir / "model_releases.db"), manager.holdout_evaluations))

权限双保险(同 ops_routes):auth_guard 中间件把 GET 判为 viewer、POST 判为 operator;
审批/激活/回滚是发布动作,路由内再强制 admin,保证单独挂载时也不越权。
账本只记录"某 scene 应使用哪个 engine+sha";服务层通过工位重载应用，响应区分账本状态与实际生效状态。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .model_release import ModelReleases, create_candidate, validate_release_model


class CandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, protected_namespaces=())
    engine_id: str = Field(min_length=1, max_length=100)
    model_sha256: str = Field(min_length=64, max_length=64)
    evaluation_id: str = Field(min_length=4, max_length=100)
    adapter: Literal["yolo_raw", "yolo_e2e", "kairos_rtdetr", "glue_yolov4"]
    classes: list[str] = Field(min_length=1, max_length=100)


class ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene: str = Field(min_length=1, max_length=160)


def _actor(request: Request) -> str:
    return (getattr(request.state, "user", None) or {}).get("username", "local")


def _require(request: Request, roles: tuple[str, ...]) -> None:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "未登录或会话已过期")
    if user.get("role") not in roles:
        raise HTTPException(403, "需要 " + "/".join(roles) + " 角色")


def router(releases: ModelReleases, evaluations, manager=None) -> APIRouter:
    """evaluations 为 holdout_routes.HoldoutEvaluations,仅依赖其 .get(report_id)。

    manager 提供时,激活/回滚成功后立即触发该场景工位热重载使账本生效;
    工位不存在(未加载)静默跳过——下次加载自会读取账本;周期进行中(409)
    返回 pending_reload=True,由调用方稍后重载。
    """
    api = APIRouter()

    def dispatch(action, *args):
        try:
            return action(*args)
        except KeyError:
            raise HTTPException(404, "发布记录不存在") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def apply_to_station(scene: str, record: dict) -> dict:
        applied = pending = False
        if manager is not None and manager.runtimes.get(scene) is not None:
            try:
                manager.reload(scene)
                active = releases.active_for(scene)
                applied = bool(active and getattr(manager.runtimes[scene], 'release_id', None) == active['id'])
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                pending = True
        return {**record, "applied_to_runtime": applied, "pending_reload": pending}

    @api.post("/api/model-releases", status_code=201)
    def create(body: CandidateBody, request: Request) -> dict:
        _require(request, ("operator", "admin"))
        try:
            return create_candidate(releases, evaluations, engine_id=body.engine_id,
                                    model_sha256=body.model_sha256, evaluation_id=body.evaluation_id,
                                    adapter=body.adapter, classes=body.classes, actor=_actor(request),
                                    engines=manager.label_engines if manager is not None else None)
        except KeyError:
            raise HTTPException(404, "评测报告不存在") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.get("/api/model-releases")
    def listing(limit: int = 50, offset: int = 0) -> dict:
        try:
            return releases.list(limit, offset)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.post("/api/model-releases/{release_id}/approve")
    def approve(release_id: str, request: Request) -> dict:
        _require(request, ("admin",))
        return dispatch(releases.approve, release_id, _actor(request))

    @api.post("/api/model-releases/{release_id}/activate")
    def activate(release_id: str, body: ActivateBody, request: Request) -> dict:
        _require(request, ("admin",))
        if manager is not None:
            candidate = dispatch(releases.get, release_id)
            if candidate['status'] != 'approved':
                raise HTTPException(400, 'Only approved releases can be activated')
            dispatch(validate_release_model, candidate)
        record = dispatch(releases.activate, release_id, body.scene, _actor(request))
        return apply_to_station(body.scene, record)

    @api.post("/api/model-releases/{release_id}/rollback")
    def rollback(release_id: str, request: Request) -> dict:
        _require(request, ("admin",))
        if manager is not None:
            current = dispatch(releases.get, release_id)
            if current.get("replaced_release_id"):
                dispatch(validate_release_model, dispatch(releases.get, current["replaced_release_id"]))
        record = dispatch(releases.rollback, release_id, _actor(request))
        return apply_to_station(record["scene"], record)

    @api.get("/api/model-releases/{release_id}")
    def detail(release_id: str) -> dict:
        try:
            record = releases.get(release_id)
            return {**record, "transitions": releases.transitions(release_id)}
        except KeyError:
            raise HTTPException(404, "发布记录不存在") from None

    return api
