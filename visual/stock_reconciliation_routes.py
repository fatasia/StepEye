"""盘点账实核对路由:期望值导入/对账为 operator,查询为 viewer。

server.py 接线(主会话执行,此处不改 server.py):

    from .stock_reconciliation_routes import router as stock_recon_router
    app.include_router(stock_recon_router(manager.stock_recon))

权限是双保险:auth_guard 中间件对 /api 前缀已按方法判 viewer/operator
(server.py),路由内再自校验一次,保证路由单独挂载(如测试或独立服务)时不越权。
对账范围:单机位可见计数对账,跨机位去重未实现(见 stock_reconciliation.SCOPE)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .stock_reconciliation import ReconcileBody, ExpectedImportBody, StockReconStore

_ROLE_RANK = {'viewer': 0, 'operator': 1, 'admin': 2}


def router(store: StockReconStore) -> APIRouter:
    api = APIRouter()

    def ensure(request: Request, need: str) -> None:
        user = getattr(request.state, 'user', None)
        if user is None:
            raise HTTPException(401, '未登录或会话已过期')
        if _ROLE_RANK.get(user.get('role'), -1) < _ROLE_RANK[need]:
            raise HTTPException(403, f'需要 {need} 及以上角色')

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ---- 期望库存 ----
    @api.get('/api/stock/expected')
    def listing(request: Request):
        ensure(request, 'viewer')
        return call(store.list_expected)

    @api.get('/api/stock/expected/{station}')
    def detail(request: Request, station: str):
        ensure(request, 'viewer')
        return call(store.get_expected, station)

    @api.get('/api/stock/expected/{station}/versions')
    def version_history(request: Request, station: str):
        ensure(request, 'viewer')
        return call(store.versions, station)

    @api.post('/api/stock/expected', status_code=201)
    def create_expected(request: Request, body: ExpectedImportBody):
        ensure(request, 'operator')
        actor = (getattr(request.state, 'user', None) or {}).get('username', 'local')
        return call(store.import_expected, body, actor)

    # ---- 对账与报告 ----
    @api.post('/api/stock/reconcile', status_code=201)
    def reconcile(request: Request, body: ReconcileBody):
        ensure(request, 'operator')
        actor = (getattr(request.state, 'user', None) or {}).get('username', 'local')
        return call(store.reconcile, body, actor)

    @api.get('/api/stock/reconciliations')
    def report_listing(request: Request, station: str = '', limit: int = 50, offset: int = 0):
        ensure(request, 'viewer')
        return call(store.list_reports, station, limit, offset)

    @api.get('/api/stock/reconciliations/{report_id}')
    def report_detail(request: Request, report_id: str):
        ensure(request, 'viewer')
        return call(store.get_report, report_id)

    return api
