"""数据范围权限:按用户 project_ids 过滤普通读接口的行。

浏览器与旧MCP令牌分别保留明确语义:
- admin:不限制,原样放行;
- 浏览器:project_ids=null 为全局，[] 为无项目；非空为项目白名单。
- 旧MCP令牌:project_ids 为空 = 不限制；非空为白名单。

浏览器scope_type区分空数组与全局，项目关联工位由browser_scope解析。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from fastapi import Depends, HTTPException

SCOPE_FIELD = "project_ids"
_UNRESTRICTED = object()


def scope_ids(user: dict | None) -> set[str] | None:
    """返回项目白名单；None 表示全局，空集合表示无项目。"""
    if user is not None and user.get("role") == "admin":
        return None
    raw = (user or {}).get(SCOPE_FIELD)
    if (user or {}).get("scope_type") == "projects":
        return set(raw or [])
    if not raw:
        return None
    if isinstance(raw, (list, tuple, set)):
        return {str(v) for v in raw}
    return {str(raw)}  # 防御:后端只会存 JSON 数组,遇到单值按单项白名单处理


def _row_identifiers(row: Any, station_key: str) -> set[str]:
    if not isinstance(row, dict):
        return set()
    return {str(row[key]) for key in (station_key, "project") if row.get(key) is not None}


def scope_filter(user: dict | None, rows: Iterable[Any], station_key: str = "station") -> list:
    """按数据范围过滤行;返回新列表,不修改入参。

    - 不限制用户(admin 或空 project_ids):原样放行,逐行保留。
    - 白名单用户:行的 station_key 字段(默认 "station")或 "project" 字段
      任一命中 project_ids 即保留;两者都缺失的行按默认拒绝滤除
      (避免无标识数据从受控接口整表外泄;未接入范围标识的接口应由
      主会话决定字段名后再接线,而不是靠本函数猜)。
    """
    allowed = scope_ids(user)
    if allowed is None:
        return list(rows)
    return [row for row in rows if _row_identifiers(row, station_key) & allowed]


def scope_summary(user: dict | None) -> dict:
    """用户可见范围描述,供前端展示;只含本人角色与本人 project_ids,不回显其他字段。"""
    allowed = scope_ids(user)
    unrestricted = allowed is None
    return {"role": (user or {}).get("role", ""),
            "unrestricted": unrestricted,
            "project_ids": [] if unrestricted else sorted(allowed or set())}


class ScopedAccess:
    """依赖注入产物:当前用户 + 其范围过滤助手。"""

    def __init__(self, user: dict):
        self.user = user

    @property
    def unrestricted(self) -> bool:
        return scope_ids(self.user) is None

    def filter(self, rows: Iterable[Any], station_key: str = "station") -> list:
        return scope_filter(self.user, rows, station_key)

    def summary(self) -> dict:
        return scope_summary(self.user)


def require_scoped(user_dep: Callable[..., dict | None]) -> Callable[..., ScopedAccess]:
    """依赖工厂:把"取当前用户"的依赖包装为"要求已登录,并返回 ScopedAccess"。

    server.py 接线示例(主会话执行;此处不改 server.py):

        from .data_scope import require_scoped
        from fastapi import Request, Depends

        def current_user(request: Request) -> dict | None:
            return getattr(request.state, "user", None)  # auth_guard 中间件已写入

        scoped = require_scoped(current_user)

        @app.get("/api/label-projects")
        def projects(user: ScopedAccess = Depends(scoped)):
            # 项目列表本身即范围主体,标识字段是 "id"
            return user.filter(manager.labels.list_projects(), station_key="id")

        @app.get("/api/alerts")
        def alerts(user: ScopedAccess = Depends(scoped)):
            # 告警行自带 station 字段,默认 station_key 即可
            return user.filter(manager.alerts.list())

    匿名(依赖返回 None)一律 401;越权判定由 scope_filter 按白名单完成,
    与 auth_guard 中间件的角色门槛(operator/admin)相互独立、可叠加。
    """

    def dependency(user: dict | None = Depends(user_dep)) -> ScopedAccess:
        if user is None:
            raise HTTPException(401, "未登录或会话已过期")
        return ScopedAccess(user)

    return dependency
