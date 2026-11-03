"""Industrial camera readback/trigger; never opens a device just to inspect status."""
from fastapi import APIRouter, HTTPException


def router(manager):
    api = APIRouter()

    @api.get('/api/stations/{sid}/camera')
    def status(sid: str):
        source = manager.get(sid).source
        if not hasattr(source, 'capabilities'):
            return {'supported': False, 'reason': '当前数据源没有工业相机控制接口'}
        return {'supported': True, **source.capabilities()}

    @api.post('/api/stations/{sid}/camera/trigger', status_code=202)
    def trigger(sid: str):
        source = manager.get(sid).source
        if not hasattr(source, 'software_trigger'):
            raise HTTPException(409, '当前数据源不支持软件触发')
        try:
            result = source.software_trigger()
        except (ValueError, RuntimeError):
            raise HTTPException(409, '相机忙、未就绪或未开启软件触发，请查看相机状态') from None
        return {**result, 'frame_received': False}

    return api
