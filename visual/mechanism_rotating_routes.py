"""轴承证据入口；报告复用现有MechanismReports，权限由服务统一中间件处理。"""
from __future__ import annotations

import importlib.util
from fastapi import APIRouter, HTTPException, Request
from .mechanism_rotating import (MAX_POINTS, MAX_SPECTRUM_POINTS, MechanismUnavailable,
                                 RotatingBody, analyze_rotating)


def rotating_router(store):
    api = APIRouter()

    @api.get('/api/mechanism/rotating/capabilities')
    def capabilities():
        return {'method': 'bearing_envelope_evidence',
                'available': importlib.util.find_spec('scipy') is not None,
                'csv_fields': ['time_s', 'acceleration'], 'max_points': MAX_POINTS,
                'min_duration_s': 1, 'max_duration_s': 10,
                'max_spectrum_points': MAX_SPECTRUM_POINTS,
                'units': ['g', 'm/s2'], 'orders_required': True,
                'production_approval': False, 'supports_rul': False,
                'supports_fault_root_cause': False, 'license': 'MIT'}

    @api.post('/api/mechanism/rotating/analyses', status_code=201)
    def create(body: RotatingBody, request: Request):
        actor = (getattr(request.state, 'user', None) or {}).get('username', 'local')
        try:
            return store.create(body, actor, kind='bearing_envelope_evidence', analyzer=analyze_rotating)
        except MechanismUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return api
