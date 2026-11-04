"""Single-image gauge evaluation, separate from realtime detection."""
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from .gauge import GaugeCalibration, GaugeError


def gauge_router(service):
    api = APIRouter()

    @api.get('/api/gauge/capabilities')
    def capabilities():
        return service.capabilities()

    @api.post('/api/gauge/trial')
    async def trial(file: UploadFile = File(...), calibration: str | None = Form(None)):
        try:
            raw = await file.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(413, '图片不能超过 10 MB')
            try:
                config = GaugeCalibration.model_validate_json(calibration) if calibration else None
            except ValueError:
                raise HTTPException(422, '请检查量程、单位和旋向') from None
            try:
                return await run_in_threadpool(service.trial, raw, config)
            except GaugeError as exc:
                raise HTTPException(exc.status, str(exc)) from None
        finally:
            await file.close()

    return api
