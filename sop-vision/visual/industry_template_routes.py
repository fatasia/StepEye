"""行业工步模板只读 API；应用走既有配方创建（steps 进 patch），不直接改工位。

模板骨架的区域是「待映射:」占位符，直接存进配方会被 StationConfig 的
zone 引用校验拒绝（有意设计）；前端必须先映射为工位实际区域。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .sop_templates import list_templates, skeleton_ids, template_steps, validate_steps


class StepsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zone_map: dict[str, str] = Field(default_factory=dict, max_length=64)


def industry_template_router() -> APIRouter:
    api = APIRouter()

    @api.get('/api/industry-templates/industries')
    def industries():
        return {'items': sorted({t['industry'] for t in list_templates()})}

    @api.get('/api/industry-templates')
    def listing(industry: str | None = None):
        try:
            return {'items': list_templates(industry or '')}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @api.get('/api/industry-templates/skeletons')
    def skeletons():
        return {'items': skeleton_ids()}

    @api.get('/api/industry-templates/{tid}/steps')
    def steps(tid: str):
        return _steps(tid, {})

    @api.post('/api/industry-templates/{tid}/steps')
    def steps_mapped(tid: str, body: StepsBody):
        return _steps(tid, body.zone_map)

    def _steps(tid: str, zone_map: dict[str, str]):
        try:
            steps_ = template_steps(tid, zone_map)
        except ValueError as exc:
            status = 404 if '模板不存在' in str(exc) else 422
            raise HTTPException(status, str(exc)) from None
        validate_steps(steps_)  # 字段漂移在这里爆炸而不是在前端
        return {'template_id': tid, 'steps': steps_}

    return api
