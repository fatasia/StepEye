"""Actuator classification reports share immutable mechanism report storage."""
from fastapi import APIRouter, HTTPException, Request
from .mechanism_actuator_classifier import ActuatorClassifierBody, ActuatorClassifierService


def actuator_classifier_router(store, service: ActuatorClassifierService):
    api = APIRouter()

    @api.get("/api/mechanism/actuator-classifier/capabilities")
    def capabilities():
        return service.capabilities()

    @api.post("/api/mechanism/actuator-classifier/analyses", status_code=201)
    def create(body: ActuatorClassifierBody, request: Request):
        actor = (getattr(request.state, "user", None) or {}).get("username", "local")
        try:
            return store.create(body, actor, kind="actuator_classifier_shadow", analyzer=service.analyze)
        except ImportError:
            raise HTTPException(503, "执行器分类需要 ONNX Runtime") from None
        except RuntimeError as error:
            raise HTTPException(429, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    return api
