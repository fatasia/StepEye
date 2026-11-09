"""运维只读路由:存储用量与配额一览(admin only)。

server.py 接线(主会话执行,此处不改 server.py):

    from .ops_routes import router as ops_router
    app.include_router(ops_router(manager.data_dir))

权限是双保险:auth_guard 中间件已把 /api/operations 前缀判为 admin
(server.py: need = "admin" if path.startswith(("/api/operations", ...))),
路由内再自校验一次,保证路由单独挂载(如测试或独立服务)时也不越权。
配额 limits 从环境/配置注入,路由本身不落数据库。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .alert_governance import save_cooldown_setting
from .storage_governance import quota_check, usage_report


class CooldownBody(BaseModel):
    cooldown_s: float = Field(ge=0, le=3600)


def router(data_dir: Path, limits: dict[str, int] | None = None,
           governance=None, counters=None, settings_path: Path | None = None) -> APIRouter:
    api = APIRouter()

    def _admin(request: Request) -> None:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "未登录或会话已过期")
        if user.get("role") != "admin":
            raise HTTPException(403, "需要 admin 及以上角色")

    @api.get("/api/operations/storage")
    def storage(request: Request) -> dict:
        _admin(request)
        # 纯读统计,无删除/清理动作;超限条目供前端标红与告警。
        return {"usage": usage_report(data_dir),
                "quota": {"limits": dict(limits or {}), "exceeded": quota_check(data_dir, limits or {})}}

    def _state() -> dict:
        if governance is None:
            raise HTTPException(503, "告警治理未接线")
        return {"cooldown_s": governance.cooldown.min_interval_s,
                "budget_dropped": governance.budget.dropped_total(),
                "cooldown_dropped": governance.cooldown.dropped_total(),
                "storm_suppressed": governance.storm.storm_stats()["suppressed_total"],
                "persisted": bool(counters and counters.snapshot())}

    @api.get("/api/operations/alert-governance")
    def governance_state(request: Request) -> dict:
        _admin(request)
        return _state()

    @api.put("/api/operations/alert-governance")
    def governance_update(body: CooldownBody, request: Request) -> dict:
        _admin(request)
        if governance is None:
            raise HTTPException(503, "告警治理未接线")
        try:
            governance.cooldown.set_interval(body.cooldown_s)
            if settings_path is not None:
                save_cooldown_setting(settings_path, body.cooldown_s)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"保存失败：{exc}") from exc
        return _state()

    return api
