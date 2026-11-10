"""动作对账账本只读 API：查看影子结果与步骤期望动作的一致性。"""
from fastapi import APIRouter, HTTPException, Query, Request


def router(ledger):
    api = APIRouter()

    def user(request: Request) -> dict:
        value = getattr(request.state, "user", None)
        if not isinstance(value, dict):
            raise HTTPException(401, "请登录后查看动作对账")
        return value

    @api.get("/api/action/ledger")
    def listing(request: Request, station: str | None = None, step: str | None = None,
                limit: int = Query(100, ge=1, le=500)):
        user(request)
        return {"items": ledger.list(station=station, step=step, limit=limit)}

    @api.get("/api/action/ledger/summary")
    def summary(request: Request, station: str | None = None):
        user(request)
        return {"items": ledger.summary(station=station)}

    return api
