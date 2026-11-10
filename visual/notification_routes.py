"""管理员通知状态接口；仅返回脱敏状态，不回传地址与凭据。"""
from fastapi import APIRouter


def router(dispatcher) -> APIRouter:
    api = APIRouter()

    @api.get("/api/notifications/status")
    def status() -> dict:
        return dispatcher.snapshot()

    return api
