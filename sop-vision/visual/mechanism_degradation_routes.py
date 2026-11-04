"""退化 RUL 请求沿用机理报告存储与平台权限。"""
from fastapi import APIRouter, HTTPException, Request
from .mechanism_degradation import DegBody, DegError


def degradation_router(store, service):
    api = APIRouter()

    @api.get('/api/mechanism/degradation/capabilities')
    def capabilities():
        return service.capabilities()

    @api.post('/api/mechanism/degradation/analyses', status_code=201)
    def create(body: DegBody, request: Request):
        actor = (getattr(request.state, 'user', None) or {}).get('username', 'local')
        try:
            return store.create(body, actor, kind='degradation_rul', analyzer=service.analyze)
        except DegError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return api
