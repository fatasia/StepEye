"""Actuator evidence reports using the shared immutable report store."""
from fastapi import APIRouter,HTTPException,Request
from .mechanism_actuator import ActuatorBody,ActuatorService

def actuator_router(store,service:ActuatorService):
    api=APIRouter()
    @api.get('/api/mechanism/actuator/capabilities')
    def capabilities():return service.capabilities()
    @api.post('/api/mechanism/actuator/analyses',status_code=201)
    def create(body:ActuatorBody,request:Request):
        actor=(getattr(request.state,'user',None) or {}).get('username','local')
        try:return store.create(body,actor,kind='actuator_motion_residual',analyzer=service.analyze)
        except ImportError:raise HTTPException(503,'Actuator SciPy dependency unavailable') from None
        except RuntimeError as e:raise HTTPException(429,str(e)) from None
        except ValueError as e:raise HTTPException(400,str(e)) from None
    return api
