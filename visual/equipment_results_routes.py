"""设备结果JSON元数据+图片同次导入；鉴权沿用主服务viewer读/operator写。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .equipment_results import EquipmentResults, MAX_IMAGES, MAX_FILE, MAX_TOTAL
from .equipment_results_types import EquipmentResultBody, EquipmentReviewBody, EquipmentRecheckBody
from .labels import LabelError


def router(store: EquipmentResults,inspection_getter=lambda:None):
    api=APIRouter()
    def actor(request):return (getattr(request.state,"user",None) or {}).get("username","local")

    @api.post("/api/equipment-results",status_code=201)
    async def ingest(request:Request,metadata:str=Form(...),files:list[UploadFile]=File(...)):
        if len(metadata.encode())>65536:raise LabelError("设备元数据最多64KB",413)
        try:body=EquipmentResultBody.model_validate(json.loads(metadata))
        except (ValueError,TypeError,ValidationError):raise LabelError("设备元数据字段、时区或格式无效") from None
        if not 1<=len(files)<=MAX_IMAGES:raise LabelError("每次需要1–5张图片")
        payloads=[];total=0
        for file in files:
            raw=await file.read(MAX_FILE+1);total+=len(raw)
            if not raw or len(raw)>MAX_FILE or total>MAX_TOTAL:raise LabelError("图片每张最多10MB、总计25MB",413)
            payloads.append((file.filename or "image",raw))
        result,created=await asyncio.to_thread(store.ingest,body,payloads,actor(request))
        return {"created":created,"result":result}

    @api.get("/api/equipment-results")
    def listing(device_id:str="",sn:str="",result:str="",limit:int=50,offset:int=0):
        return store.list(device_id,sn,result,limit,offset)

    @api.get("/api/equipment-results/{result_id}")
    def detail(result_id:str):return store.get(result_id)

    @api.get("/api/equipment-results/{result_id}/images/{index}")
    def image(result_id:str,index:int):return FileResponse(store.image_path(result_id,index),media_type="image/png")

    @api.post("/api/equipment-results/{result_id}/reviews",status_code=201)
    def review(result_id:str,body:EquipmentReviewBody,request:Request):return store.review(result_id,body,actor(request))

    @api.post("/api/equipment-results/{result_id}/rechecks",status_code=202)
    def recheck(result_id:str,body:EquipmentRecheckBody,request:Request):
        return store.recheck(result_id,body,actor(request),inspection_getter())

    @api.get("/api/equipment-results/{result_id}/reviews")
    def reviews(result_id:str):
        try:inspection=inspection_getter()
        except (LabelError,HTTPException) as exc:
            status=exc.status if isinstance(exc,LabelError) else exc.status_code
            if status not in {409,503}:raise
            inspection=None  # 人工复判与原判查询不依赖VLM当前配置。
        return store.reviews(result_id,inspection)

    return api
