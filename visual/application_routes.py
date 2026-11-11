"""应用工作流 API；复用现有模型、工位和标注资源。"""
import json
import uuid
from typing import Literal
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .applications import ApplicationBody, InputBody


class RunBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    production: bool = False
    confirmed: bool = False


class ExposureBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['off','auto']


class WvpSourceBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device_id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    channel_id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')


def router(service):
    api = APIRouter()

    def call(fn, *args):
        try:
            return fn(*args)
        except ValidationError:
            raise HTTPException(409, '应用配置无效，请检查输入源、规则和模型设置') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @api.get('/api/applications')
    def listing(request: Request, include_archived:bool=False):
        from .browser_scope import station_ids
        allowed=station_ids(request.state.user,service.manager.labels)
        return [r for r in service.list(include_archived) if allowed is None or r['id'] in allowed]

    @api.post('/api/applications', status_code=201)
    def create(body: ApplicationBody):
        return call(service.create, body)

    @api.get('/api/applications/{aid}')
    def detail(aid: str):
        return call(lambda: service.public(service.get(aid)))

    @api.post('/api/applications/{aid}/input')
    def source(aid: str, body: InputBody):
        return call(service.probe, aid, body.model_dump())

    @api.put('/api/applications/{aid}/exposure')
    def exposure(aid: str, body: ExposureBody):
        return call(service.set_exposure,aid,body.mode)

    @api.post('/api/applications/{aid}/gb28181')
    def gb28181_source(aid: str,body: WvpSourceBody,request: Request):
        from .setup_routes import require_setup_admin, wvp_options
        from .gb28181 import WvpClient
        require_setup_admin(request)
        row=call(service.get,aid)
        if row['state'] in ('trial','running','archived'):
            raise HTTPException(409,'请先暂停应用再更换输入源')
        cfg=wvp_options(service.manager)
        if not cfg['url']:raise HTTPException(409,'请先在设置中配置国标平台')
        try:
            stream=WvpClient(cfg['url'],cfg['username'],cfg['password']).play_url(body.device_id,body.channel_id).get('rtsp')
        except Exception:
            raise HTTPException(409,'国标平台点播失败，请检查平台、设备和通道') from None
        if not isinstance(stream,str) or not stream.lower().startswith(('rtsp://','rtsps://')):
            raise HTTPException(409,'国标平台未返回 RTSP 地址，请检查媒体服务')
        return call(service.probe,aid,{'type':'rtsp','uri':stream,
                    'wvp':{'device_id':body.device_id,'channel_id':body.channel_id}})

    @api.post('/api/applications/{aid}/video')
    async def video(aid: str, file: UploadFile = File(...)):
        call(service.get, aid)
        if not (file.filename or '').lower().endswith(('.mp4', '.avi', '.mov', '.mkv')):
            raise HTTPException(400, '请选择 MP4、AVI、MOV 或 MKV 视频')
        target = service.root / (uuid.uuid4().hex + '.mp4')
        size = 0
        try:
            with target.open('wb') as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, '视频最大100MB')
                    stream.write(chunk)
            return call(service.probe, aid, {'type':'file', 'uri':str(target.resolve())})
        except Exception:
            target.unlink(missing_ok=True)
            raise

    @api.post('/api/applications/{aid}/run')
    def run(aid: str, body: RunBody):
        row = call(service.get, aid)
        if body.production and (not body.confirmed or row['state'] not in ('trial','running')):
            raise HTTPException(409, '先试运行并确认效果，再正式启用')
        if not body.production and row['state']=='running':
            raise HTTPException(409,'请先暂停正在运行的应用')
        return call(service.start, aid, not body.production)

    @api.post('/api/applications/{aid}/pause')
    def pause(aid: str):
        return call(service.pause, aid)

    @api.delete('/api/applications/{aid}')
    def archive(aid:str):
        return call(service.archive,aid)

    @api.post('/api/applications/{aid}/restore')
    def restore(aid:str):
        return call(service.pause,aid)

    @api.get('/api/applications/{aid}/events')
    def events(aid: str):
        call(service.get, aid)
        return service.events(aid)

    @api.post('/api/applications/{aid}/project')
    def project(aid: str):
        row = call(service.get, aid)
        if not row['project_id']:
            raise HTTPException(409, '当前应用没有框标注模型，请先使用良品检测或选择检测模型')
        return {'project_id':row['project_id'], 'engine_id':row['engine_id']}

    return api
