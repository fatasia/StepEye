"""Reference-card endpoints; writes remain subject to common operator RBAC."""
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .action_reference_check import evaluate as evaluate_reference_checks


class ReferenceBody(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    interval_index: int = Field(ge=0,le=10000)
    station_id: str = Field(min_length=1,max_length=160)
    step_id: str = Field(min_length=1,max_length=160)
    key_time_s: float = Field(ge=0)


class ReferenceCheckBody(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    mapping: dict[str,str] = Field(default_factory=dict,max_length=200)
    observations: list[dict] = Field(default_factory=list,max_length=2000)
    min_conf: float = Field(default=.6,gt=0,le=1,allow_inf_nan=False)


def router(store, manager):
    api = APIRouter()

    def call(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None

    @api.post('/api/timelines/{tid}/action-references',status_code=201)
    def create(tid:str,body:ReferenceBody,request:Request):
        rt = manager.runtimes.get(body.station_id)
        if rt is None:
            raise HTTPException(404,'工位不存在')
        step = next((s for s in rt.program.steps if s.id == body.step_id),None)
        if step is None:
            raise HTTPException(422,'目标 SOP 步骤不存在')
        user = getattr(request.state,'user',None)
        if not isinstance(user,dict) or user.get('role') not in ('operator','admin'):
            raise HTTPException(403,'需要操作员权限')
        return call(store.create,tid,body.interval_index,body.station_id,body.step_id,step.name,
                    body.key_time_s,rt.program.rules_hash,user['username'])

    @api.get('/api/timelines/{tid}/action-references')
    def timeline_list(tid:str):
        return {'items':store.list(timeline_id=tid)}

    @api.get('/api/stations/{sid}/action-references')
    def station_list(sid:str):
        return {'items':store.list(station_id=sid)}

    @api.get('/api/action-references/{rid}/frames/{role}')
    def frame(rid:str,role:Literal['start','key','end']):
        return FileResponse(call(store.frame,rid,role),media_type='image/png')

    # ---- 参考卡 × 动作影子对账(影子默认关;判定仅记录,不进 SOP 条件) ----
    @api.post('/api/stations/{sid}/action-reference-checks',status_code=201)
    def run_reference_checks(sid:str,body:ReferenceCheckBody,request:Request):
        user = getattr(request.state,'user',None)
        if not isinstance(user,dict) or user.get('role') not in ('operator','admin'):
            raise HTTPException(403,'需要操作员权限')
        verdicts = call(evaluate_reference_checks,store.list(station_id=sid),
                        body.mapping,body.observations,min_conf=body.min_conf)
        items = [manager.reference_checks.create(v)[0] for v in verdicts]
        return {'items':items}

    @api.get('/api/stations/{sid}/action-reference-checks')
    def reference_checks_list(sid:str,limit:int=Query(100,ge=1,le=500)):
        return {'items':manager.reference_checks.list(sid,limit)}

    return api
