"""管理员 AI 服务配置 API；鉴权由宿主服务中间件统一执行。"""
from fastapi import APIRouter, Body, HTTPException
from .ai_services import AIServiceStore, AIServiceError


def router(store: AIServiceStore):
    api=APIRouter()
    def call(fn,*args):
        try:return fn(*args)
        except AIServiceError as exc:raise HTTPException(exc.status,str(exc)) from None
    @api.get('/api/ai/services')
    def catalog():return call(store.catalog)
    @api.put('/api/ai/services/{sid}')
    def update(sid:str,body:dict=Body(...)):return call(store.update,sid,body)
    @api.put('/api/ai/bindings')
    def bindings(body:dict=Body(...)):return call(store.bind,body)
    @api.post('/api/ai/services/{sid}/test')
    def test(sid:str,body:dict=Body(default={})):
        if set(body)-{'purpose'}:raise HTTPException(400,'测试字段无效')
        return call(store.test,sid,body.get('purpose','chat'))
    return api
