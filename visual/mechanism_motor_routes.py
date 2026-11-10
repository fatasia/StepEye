"""Motor research reports reuse the existing mechanism report storage."""
from fastapi import APIRouter, HTTPException, Request
from .mechanism_motor import MotorBody, MotorService


def motor_router(store, service: MotorService):
    api = APIRouter()

    @api.get("/api/mechanism/motor/capabilities")
    def capabilities():
        return service.capabilities()

    @api.post("/api/mechanism/motor/analyses", status_code=201)
    def create(body: MotorBody, request: Request):
        actor = (getattr(request.state, "user", None) or {}).get("username", "local")
        try:
            return store.create(body, actor, kind="motor_rf_shadow", analyzer=service.analyze)
        except ImportError:
            raise HTTPException(503, "电机推理需要 ONNX Runtime") from None
        except RuntimeError as error:
            raise HTTPException(429, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    return api
