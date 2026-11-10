"""数据范围权限:admin 放行 / 白名单过滤 / 空范围放行 / 范围描述不泄露 / 依赖 401。"""
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from visual.data_scope import ScopedAccess, require_scoped, scope_filter, scope_summary

ROWS = [{"station": "s1", "kind": "a"}, {"station": "s2"}, {"project": "s1"},
        {"project": "s2"}, {}, "not-a-dict"]
OPERATOR = {"username": "u", "role": "operator", "project_ids": ["s1"]}


def test_admin_passes_everything():
    assert scope_filter({"role": "admin", "project_ids": ["s9"]}, ROWS) == ROWS
    assert scope_filter({"role": "admin"}, ROWS) == ROWS
    # admin 的 project_ids 不参与过滤,也不能被白名单语义误伤
    assert scope_filter({"role": "admin", "project_ids": []}, []) == []


def test_whitelist_filters_station_and_project_fields():
    kept = scope_filter(OPERATOR, ROWS)
    assert kept == [{"station": "s1", "kind": "a"}, {"project": "s1"}]
    # 标识字段缺失或非 dict 的行按默认拒绝滤除,绝不整表外泄
    assert all(isinstance(r, dict) for r in kept)
    # 自定义标识字段:标注项目列表的行以 "id" 为主体(对齐 label_routes 的 r["id"])
    projects = [{"id": "s1", "name": "a"}, {"id": "s2", "name": "b"}]
    assert scope_filter(OPERATOR, projects, station_key="id") == [projects[0]]
    # 返回新列表,不改入参
    assert len(ROWS) == 6


def test_empty_scope_means_unrestricted():
    for user in ({"role": "operator", "project_ids": []}, {"role": "viewer"}, None):
        assert scope_filter(user, ROWS) == ROWS
    # MCP 令牌用户(verify_mcp_token 映射 role="viewer" + project_ids)走白名单;
    # station 与 project 字段任一命中即保留
    mcp = {"role": "viewer", "project_ids": ["s2"], "mcp_token_id": "x", "scopes": ["read"]}
    assert scope_filter(mcp, ROWS) == [{"station": "s2"}, {"project": "s2"}]


def test_summary_only_own_scope():
    summary = scope_summary(OPERATOR)
    assert summary == {"role": "operator", "unrestricted": False, "project_ids": ["s1"]}
    # 不回显其他字段(不泄露 scopes/mcp_token_id/他人范围)
    poisoned = {**OPERATOR, "scopes": ["read"], "mcp_token_id": "t1", "other_users": ["admin"]}
    assert set(scope_summary(poisoned)) == {"role", "unrestricted", "project_ids"}
    assert scope_summary(poisoned)["project_ids"] == ["s1"]
    # 不限制用户与 admin:不列举 ids
    assert scope_summary({"role": "viewer"})["unrestricted"] is True
    assert scope_summary({"role": "viewer"})["project_ids"] == []
    assert scope_summary({"role": "admin", "project_ids": ["s1"]}) == \
        {"role": "admin", "unrestricted": True, "project_ids": []}


def _app() -> FastAPI:
    def current_user(request: Request) -> dict | None:
        return getattr(request.state, "user", None)

    scoped = require_scoped(current_user)
    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        role = request.headers.get("x-test-role", "")
        request.state.user = None if not role else {"username": "t", "role": role, "project_ids": ["s1"]}
        return await call_next(request)

    @app.get("/rows")
    def rows(user: ScopedAccess = Depends(scoped)):
        return user.filter(ROWS)

    @app.get("/me")
    def me(user: ScopedAccess = Depends(scoped)):
        return user.summary()

    return app


def test_require_scoped_401_and_filter():
    client = TestClient(_app())
    assert client.get("/rows").status_code == 401
    assert client.get("/rows", headers={"x-test-role": "operator"}).json() == \
        [{"station": "s1", "kind": "a"}, {"project": "s1"}]
    assert client.get("/rows", headers={"x-test-role": "admin"}).status_code == 200
    assert client.get("/me", headers={"x-test-role": "operator"}).json()["project_ids"] == ["s1"]
