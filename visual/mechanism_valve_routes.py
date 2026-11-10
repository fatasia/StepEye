"""阀门影子分析路由；沿用统一鉴权和机理不可变报告。"""
from fastapi import APIRouter, HTTPException, Request
from .mechanism_valve import ValveBody, ValveError


def valve_router(store, shadow):
    api = APIRouter()

    @api.get('/api/mechanism/valve/capabilities')
    def capabilities():
        return shadow.capabilities()

    @api.post('/api/mechanism/valve/analyses', status_code=201)
    def create(body: ValveBody, request: Request):
        actor = (getattr(request.state, 'user', None) or {}).get('username', 'local')
        try:
            return store.create(body, actor, kind='valve_fusion_shadow', analyzer=shadow.analyze)
        except ValveError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return api
