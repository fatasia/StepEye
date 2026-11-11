"""SOP 表单编辑只改业务字段；通过现有配方版本与回滚路径生效。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
import math
import yaml

from .recipes import RecipeBody, RecipeService, RecipeStore


class StepsEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    steps: list[dict] = Field(min_length=1, max_length=64)
    cycle: dict
    roi_states: list[dict] = Field(default_factory=list, max_length=64)


def router(manager):
    api = APIRouter()

    @api.get('/api/stations/{sid}/steps-editor')
    def detail(sid: str):
        if sid not in manager.paths:
            raise HTTPException(404, '工位不存在')
        raw = yaml.safe_load(manager.paths[sid].read_text(encoding='utf8'))
        return {k:raw.get(k, [] if k in ('steps','zones','roi_states','signal_sources') else {})
                for k in ('steps','cycle','zones','roi_states','signal_sources')}

    @api.put('/api/stations/{sid}/steps-editor')
    def update(sid: str, body: StepsEdit, request: Request):
        if any(s.get('timeout_s') is not None and (not isinstance(s['timeout_s'],(int,float)) or not math.isfinite(s['timeout_s']) or s['timeout_s']<=0) for s in body.steps):
            raise HTTPException(400,'步骤超时须为正数，留空表示不限制')
        if sid not in manager.runtimes:
            raise HTTPException(404,'请先启动工位试运行')
        if manager.runtimes[sid].engine.cycle is not None:
            raise HTTPException(409,'当前工件周期进行中，请等待周期结束后保存')
        store = RecipeStore(manager.data_dir / 'recipes.db')
        try:
            service = RecipeService(store, manager)
            recipe = service.create(RecipeBody(name='SOP 表单修改', model_key=sid,
                station_id=sid, patch=body.model_dump()), request.state.user['username'])
            applied = service.switch(sid, recipe['latest_version']['id'], request.state.user['username'])
            return {'ok':True, 'version_id':recipe['latest_version']['id'], 'status':applied['status']}
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from None
        finally:
            store.close()
    return api
