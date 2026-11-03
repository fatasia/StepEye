"""Shared atomic station configuration writes for proximity and safety settings."""
import threading
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .dsl import StationConfig, Program
from .proximity import ProximityCfg, ProximityMonitor
from .ppe import PPECfg
from .thermal import ThermalCfg
from .ehs import EhsCfg
from .recipes import _atomic_write


class SafetyConfigBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ppe: PPECfg
    thermal: ThermalCfg
    proximity: ProximityCfg


def router(manager, calibrations):
    api = APIRouter()
    lock = threading.Lock()

    def runtime(sid):
        if sid not in manager.runtimes:
            raise HTTPException(404, '工位不存在')
        return manager.runtimes[sid]

    def save(sid, fields):
        # All UI patches use this same validated atomic-write/reload/restore path.
        with lock:
            rt = runtime(sid)
            if rt.engine.cycle is not None:
                raise HTTPException(409, '周期进行中，不能切换安全规则')
            path = manager.paths[sid]
            original = path.read_bytes()
            raw = yaml.safe_load(original)
            if raw.get('station') != sid:
                raise HTTPException(409, '工位配置 id 不匹配')
            raw.update(fields)
            try:
                cfg = StationConfig.model_validate(raw)
                Program.from_config(cfg)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if cfg.proximity.enabled:
                monitor = ProximityMonitor(cfg.proximity, (rt.w, rt.h))
                monitor.bind_calibrations(calibrations)
                if monitor.record is None:
                    raise HTTPException(422, monitor.binding_error)
            _atomic_write(path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode('utf-8'))
            try:
                manager.reload(sid)
                runtime(sid).bind_calibrations(calibrations)
            except Exception as exc:
                try:
                    _atomic_write(path, original)
                    manager.reload(sid)
                    runtime(sid).bind_calibrations(calibrations)
                except Exception as restore:
                    raise HTTPException(500, '配置启用失败且恢复失败，工位需检查') from restore
                raise HTTPException(409, '配置启用失败，原配置已恢复') from exc

    @api.get('/api/stations/{sid}/proximity')
    def get_config(sid: str):
        rt = runtime(sid)
        return {'config': rt.cfg.proximity.model_dump(mode='json'), 'state': rt.proximity.snapshot()}

    @api.put('/api/stations/{sid}/proximity')
    def put_config(sid: str, body: ProximityCfg):
        save(sid, {'proximity': body.model_dump(mode='json')})
        return get_config(sid)

    @api.get('/api/stations/{sid}/safety-config')
    def get_safety(sid: str):
        rt = runtime(sid)
        return {**{name:getattr(rt.cfg,name).model_dump(mode='json') for name in ('ppe','thermal','proximity')},
                'zones':[{'id':zone.id} for zone in rt.cfg.zones], 'image_size':[rt.w,rt.h],
                'signal_sources':rt.cfg.signal_sources}

    @api.put('/api/stations/{sid}/safety-config')
    def put_safety(sid: str, body: SafetyConfigBody):
        save(sid, body.model_dump(mode='json'))
        return get_safety(sid)

    @api.get('/api/stations/{sid}/ehs-config')
    def get_ehs(sid: str):
        rt = runtime(sid)
        return {'config': rt.cfg.ehs.model_dump(mode='json'),
                'zones': [zone.id for zone in rt.cfg.zones],
                'labels': list(rt.cfg.detector.classes),
                'state': rt.ehs.snapshot() if getattr(rt, 'ehs', None) is not None else {'enabled': False, 'rules': {}}}

    @api.put('/api/stations/{sid}/ehs-config')
    def put_ehs(sid: str, body: EhsCfg):
        save(sid, {'ehs': body.model_dump(mode='json')})
        return get_ehs(sid)

    return api
