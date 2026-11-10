"""Admin-only policy writes; health samples never come from HTTP clients."""
from fastapi import APIRouter, HTTPException, Request
from .release_monitor import ArmBody
from .model_release_routes import _require, _actor


def router(monitor):
    api = APIRouter()

    @api.get('/api/model-release-monitor/{scene}')
    def get(scene: str, request: Request):
        _require(request, ('viewer', 'operator', 'admin'))
        state = monitor.get(scene)
        if state is None:
            raise HTTPException(404, '该工位未配置发布监控')
        return state

    @api.put('/api/model-release-monitor/{scene}')
    def arm(scene: str, body: ArmBody, request: Request):
        _require(request, ('admin',))
        try:
            return monitor.arm(scene, body.release_id, body.policy.model_dump(), actor=_actor(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    return api
