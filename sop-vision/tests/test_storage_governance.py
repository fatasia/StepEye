"""存储治理:用量统计数值、超限判定、/api/operations/storage 路由 401/403/200。"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from visual.ops_routes import router
from visual.storage_governance import ROOT_FILES_BUCKET, quota_check, usage_report


def make_data(tmp_path):
    """固定字节数,统计值可精确断言:evidence 160B/3 文件,labeling 30B/1,根文件 200B/1。"""
    (tmp_path / "evidence" / "frames").mkdir(parents=True)
    (tmp_path / "evidence" / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "evidence" / "b.bin").write_bytes(b"y" * 50)
    (tmp_path / "evidence" / "frames" / "c.bin").write_bytes(b"z" * 10)
    (tmp_path / "labeling").mkdir()
    (tmp_path / "labeling" / "d.bin").write_bytes(b"w" * 30)
    (tmp_path / "operations.db").write_bytes(b"q" * 200)
    return tmp_path


def test_usage_report_counts_bytes_and_files(tmp_path):
    report = usage_report(make_data(tmp_path))
    items = {i["name"]: i for i in report["items"]}
    assert items["evidence"] == {"name": "evidence", "bytes": 160, "files": 3}
    assert items["labeling"] == {"name": "labeling", "bytes": 30, "files": 1}
    assert items[ROOT_FILES_BUCKET] == {"name": ROOT_FILES_BUCKET, "bytes": 200, "files": 1}
    assert report["total_bytes"] == 390 and report["total_files"] == 5
    assert report["exists"] is True


def test_usage_report_empty_and_missing_dir(tmp_path):
    empty = usage_report(tmp_path / "nothing-here")
    assert empty == {"items": [], "total_bytes": 0, "total_files": 0, "exists": False}
    fresh_dir = tmp_path / "fresh"
    fresh_dir.mkdir()
    fresh = usage_report(fresh_dir)
    assert fresh["items"] == [] and fresh["exists"] is True and fresh["total_bytes"] == 0


def test_quota_check_over_limit(tmp_path):
    root = make_data(tmp_path)
    assert quota_check(root, {"evidence": 100}) == [{"name": "evidence", "bytes": 160, "limit": 100}]
    assert quota_check(root, {"evidence": 160}) == []        # 等于上限不算超限
    assert quota_check(root, {"evidence": 10**9, "labeling": 10**9}) == []
    assert quota_check(root, {"不存在目录": 0}) == []          # 未知键按 0 字节计,不误报
    exceeded = quota_check(root, {"evidence": 10, ROOT_FILES_BUCKET: 10})
    assert [e["name"] for e in exceeded] == [ROOT_FILES_BUCKET, "evidence"]  # 按超限倍率降序
    with pytest.raises(ValueError):
        quota_check(root, {"evidence": -1})
    assert quota_check(root, {}) == []


def _client(tmp_path, limits=None):
    app = FastAPI()
    app.include_router(router(tmp_path, limits))

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        role = request.headers.get("x-test-role", "")
        request.state.user = None if not role else {"username": "t", "role": role, "project_ids": []}
        return await call_next(request)

    return TestClient(app)


def test_storage_route_auth(tmp_path):
    client = _client(make_data(tmp_path), {"evidence": 100})
    assert client.get("/api/operations/storage").status_code == 401
    for role in ("viewer", "operator"):
        assert client.get("/api/operations/storage", headers={"x-test-role": role}).status_code == 403
    resp = client.get("/api/operations/storage", headers={"x-test-role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert {i["name"] for i in body["usage"]["items"]} >= {"evidence", "labeling", ROOT_FILES_BUCKET}
    assert body["quota"]["limits"] == {"evidence": 100}
    assert body["quota"]["exceeded"] == [{"name": "evidence", "bytes": 160, "limit": 100}]


def test_storage_route_without_limits(tmp_path):
    missing_dir = tmp_path / "not-created-yet"  # 新装环境:目录还没建,返回空报告而不是 500
    body = _client(missing_dir).get("/api/operations/storage", headers={"x-test-role": "admin"}).json()
    assert body["quota"] == {"limits": {}, "exceeded": []}
    assert body["usage"]["exists"] is False
