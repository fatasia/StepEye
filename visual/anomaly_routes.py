import asyncio
import json
import re
import hashlib
import base64
from typing import Literal
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


class ThresholdBody(BaseModel):
    threshold: float = Field(ge=.02,le=1,allow_inf_nan=False)
    lighting: Literal['off','auto'] | None = None


def router(service):
    api=APIRouter()
    async def call(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except (ValueError,FileNotFoundError) as exc:raise HTTPException(409,str(exc)) from None

    @api.get('/api/anomaly/scenes')
    async def listing(request:Request):
        from .browser_scope import station_ids
        allowed=station_ids(request.state.user,service.manager.labels)
        return [r for r in await call(service.list) if allowed is None or r['scene'] in allowed]

    @api.get('/api/anomaly/{scene}/details')
    async def details(scene:str):
        path=await call(service.path,scene)
        refs=json.loads((path/'refs.json').read_text()) if (path/'refs.json').exists() else []
        return {'refs':refs,'versions':service.versions(scene),'history':await call(service.history,scene)}

    @api.get('/api/anomaly/{scene}/images/{name}')
    def image(scene:str,name:str):
        if not re.fullmatch(r'[a-f0-9]{32}(?:[a-f0-9]{32})?\.jpg',name):raise HTTPException(400,'图片名称无效')
        try:path=service.path(scene)/name
        except ValueError:raise HTTPException(400,'场景名称无效') from None
        if not path.is_file():raise HTTPException(404,'图片不存在')
        return FileResponse(path,media_type='image/jpeg')

    @api.post('/api/anomaly/{scene}/register',status_code=201)
    async def register(scene:str,files:list[UploadFile]=File(...),threshold:float=Form(.12),backend:str|None=Form(None),lighting:str|None=Form(None)):
        payloads=[await f.read(20*1024*1024+1) for f in files[:9]]
        return await call(service.register,scene,payloads,threshold,backend,lighting)

    @api.post('/api/anomaly/{scene}/exposure-preview')
    async def exposure_preview(scene:str,image:UploadFile=File(...)):
        import cv2
        from .anomaly import normalize_exposure
        await call(service.path,scene)
        raw=await image.read(20*1024*1024+1)
        img=await call(service.image,raw)
        adjusted,info=await asyncio.to_thread(normalize_exposure,img)
        ok,jpg=cv2.imencode('.jpg',adjusted,[cv2.IMWRITE_JPEG_QUALITY,85])
        if not ok:raise HTTPException(409,'图片预览失败')
        return {'image':base64.b64encode(jpg).decode('ascii'),**info}

    @api.post('/api/anomaly/{scene}/inspect',status_code=201)
    async def inspect(scene:str,image:UploadFile=File(...)):
        return await call(service.inspect,scene,await image.read(20*1024*1024+1))

    @api.put('/api/anomaly/{scene}/settings')
    async def settings(scene:str,body:ThresholdBody):return await call(service.settings,scene,body.threshold,body.lighting)

    @api.delete('/api/anomaly/{scene}/refs/{ref}')
    async def remove_reference(scene:str,ref:str):return await call(service.remove_reference,scene,ref)

    @api.post('/api/anomaly/{scene}/versions/{version}/restore')
    async def restore(scene:str,version:str):return await call(service.restore,scene,version)

    @api.post('/api/anomaly/{scene}/calibrate')
    async def calibrate(scene:str,normal:UploadFile=File(...),abnormal:UploadFile=File(...)):
        good_bytes=await normal.read(20*1024*1024+1)
        bad_bytes=await abnormal.read(20*1024*1024+1)
        path=await call(service.path,scene)
        refs=json.loads((path/'refs.json').read_text()) if (path/'refs.json').exists() else []
        if good_bytes==bad_bytes or any(hashlib.sha256(b).hexdigest() in refs for b in (good_bytes,bad_bytes)):
            raise HTTPException(409,'请选择未用于建库的不同正常、异常图片')
        good=await call(service.inspect,scene,good_bytes,False)
        bad=await call(service.inspect,scene,bad_bytes,False)
        if good['score']>=bad['score'] or good['score']>=1 or bad['score']<.02:
            return {'separable':False,'message':'这两张样本无法区分，请补充不同批次样本或调整拍摄区域'}
        return {'separable':True,'suggested':round(max(.02,min(1,(good['score']+bad['score'])/2)),4),
                'message':'根据本次核对样本建议阈值；保存前请再用其他批次核对'}
    return api
