"""Document APIs with explicit role filtering, including assistant retrieval."""
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from typing import Literal
from .documents import DocumentError, authorize
from .documents_parse import MAX_BYTES


class StatusBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['published','archived']


def router(store):
    api=APIRouter()

    def user(request):
        value=getattr(request.state,'user',None)
        if not isinstance(value,dict):
            raise HTTPException(401,'请登录后使用文档中心')
        try:
            authorize(value.get('role'))
        except DocumentError as exc:
            raise HTTPException(exc.status,str(exc)) from None
        return value

    def call(fn,*args,**kwargs):
        try:
            return fn(*args,**kwargs)
        except DocumentError as exc:
            raise HTTPException(exc.status,str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None

    @api.get('/api/documents')
    def listing(request:Request,document_id:str|None=None):
        return {'items':call(store.list,user(request)['role'],document_id)}

    @api.get('/api/documents/search')
    def search(request:Request,q:str=Query(min_length=1,max_length=200),limit:int=Query(5,ge=1,le=10)):
        return {'items':call(store.search,q,user(request)['role'],limit)}

    @api.post('/api/documents',status_code=201)
    def upload(request:Request,file:UploadFile=File(...),title:str=Form(...),station:str=Form(''),
               device:str=Form(''),product:str=Form(''),document_id:str|None=Form(None)):
        actor=user(request)
        call(authorize,actor['role'],write=True)
        data=file.file.read(MAX_BYTES+1)
        return call(store.upload,file.filename or '',data,{'title':title,'station':station,'device':device,'product':product},
                    actor['role'],actor['username'],document_id)

    @api.get('/api/documents/versions/{vid}')
    def version(vid:str,request:Request):
        role=user(request)['role']
        return {'version':call(store.get_version,vid,role),'paragraphs':call(store.paragraphs,vid,role)}

    @api.get('/api/documents/versions/{vid}/original')
    def original(vid:str,request:Request):
        record,path=call(store.original,vid,user(request)['role'])
        return FileResponse(path,media_type='application/octet-stream',filename=record['filename'],
                            headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox"})

    @api.put('/api/documents/versions/{vid}/status')
    def status(vid:str,body:StatusBody,request:Request):
        return call(store.set_status,vid,body.status,user(request)['role'])

    return api
