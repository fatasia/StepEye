"""Planar calibration endpoints; server middleware supplies authentication/RBAC."""
from fastapi import APIRouter, HTTPException
from .calibration import CalibrationBody, MeasureBody


def router(store):
    api = APIRouter()

    def call(fn, *args):
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @api.get("/api/calibrations")
    def listing(scope_id: str | None = None):
        return store.list(scope_id)

    @api.post("/api/calibrations", status_code=201)
    def create(body: CalibrationBody):
        return call(store.create, body)

    @api.get("/api/calibrations/{calibration_id}")
    def detail(calibration_id: str):
        return call(store.get, calibration_id)

    @api.post("/api/calibrations/{calibration_id}/measure")
    def measure(calibration_id: str, body: MeasureBody):
        return call(store.measure, calibration_id, body)

    return api
