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

from .storage_governance import quota_check, usage_report


def router(data_dir: Path, limits: dict[str, int] | None = None) -> APIRouter:
    api = APIRouter()

    @api.get("/api/operations/storage")
    def storage(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(401, "未登录或会话已过期")
        if user.get("role") != "admin":
            raise HTTPException(403, "需要 admin 及以上角色")
        # 纯读统计,无删除/清理动作;超限条目供前端标红与告警。
        return {"usage": usage_report(data_dir),
                "quota": {"limits": dict(limits or {}), "exceeded": quota_check(data_dir, limits or {})}}

    return api
