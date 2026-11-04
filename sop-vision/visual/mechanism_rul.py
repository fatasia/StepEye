"""NASA FD001-only RUL research inference, isolated per request."""
from __future__ import annotations
import hashlib,json,subprocess,sys,threading,tempfile
from pathlib import Path
from typing import Literal,Annotated
from pydantic import BaseModel,ConfigDict,Field,model_validator
import numpy as np

MODEL_SHA='e859f24836a4a40a35d3c45b34d7646c14eb88223e80f04051740cab3f3c252c'
MANIFEST_SHA='0b5fbc6a6301aad5550abbbadc7e95e8e5fe1a721d265f658a2e724afd4b34b4'
CHANNELS=[f'setting{i}' for i in range(1,4)]+[f'sensor{i}' for i in range(1,22)]

class RULError(RuntimeError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status

Finite = Annotated[float,Field(strict=True,allow_inf_nan=False)]
Row = Annotated[list[Finite],Field(min_length=24,max_length=24)]

class RULWindow(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    unit_id:str=Field(min_length=1,max_length=100)
    end_cycle:int=Field(ge=30,le=1000000,strict=True)
    values:list[Row]=Field(min_length=30,max_length=30)
    @model_validator(mode='after')
    def shape(self):
        if any(len(row)!=24 for row in self.values):raise ValueError('Each cycle requires 24 channels')
        return self

class RULBody(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    title:str=Field(default='',max_length=200)
    dataset:Literal['NASA-CMAPSS-FD001']
    unit:Literal['cycle']='cycle'
    channels:list[str]=Field(min_length=24,max_length=24)
    windows:list[RULWindow]=Field(min_length=1,max_length=8)
    @model_validator(mode='after')
    def contract(self):
        if self.channels!=CHANNELS:raise ValueError('NASA FD001 channel order required')
        keys=[(w.unit_id,w.end_cycle) for w in self.windows]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate unit/cycle window')
        return self

def pinned_bundle(root):
    bundle=Path(root)/'models/mechanism/cmapss-rul'
    for name,digest in [('model.onnx',MODEL_SHA),('manifest.json',MANIFEST_SHA)]:
        path=bundle/name
        if not path.is_file() or path.is_symlink():raise RULError('RUL asset unavailable',503)
        with path.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=digest:raise RULError('RUL asset fingerprint mismatch',409)
    return bundle

class RULService:
    def __init__(self,base_dir,*,trusted_public_keys=None):
        self.root=Path(base_dir).resolve();self.keys=trusted_public_keys
        self.lock=threading.Lock();self.closed=False
    def capabilities(self):
        try:pinned_bundle(self.root);available=True;reason=''
        except RULError as e:available=False;reason=str(e)
        return {'available':available and not self.closed,'reason':reason,'dataset':'NASA-CMAPSS-FD001',
                'unit':'cycle','channels':CHANNELS,'window_cycles':30,'max_windows':8,
                'model_sha256':MODEL_SHA,'scope':'simulated turbofan only; not industrial motor RUL',
                'signature_required':self.keys is not None}
    def analyze(self,body):
        body=body if isinstance(body,RULBody) else RULBody.model_validate(body)
        if self.closed:raise RULError('RUL service closed',503)
        if not self.lock.acquire(False):raise RULError('RUL service busy',429)
        try:
            pinned_bundle(self.root)
            payload={'root':str(self.root),'body':body.model_dump(),'trusted_public_keys':self.keys}
            try:
                with tempfile.TemporaryDirectory(prefix='stepeye-rul-') as temporary:
                    request=Path(temporary)/'request.json'
                    encoded=json.dumps(payload,allow_nan=False).encode('utf-8')
                    if len(encoded)>512*1024:raise RULError('RUL request exceeds IPC limit',413)
                    request.write_bytes(encoded)
                    p=subprocess.run([sys.executable,'-m','visual.mechanism_rul_worker','--request',str(request)],
                                     stdin=subprocess.DEVNULL,text=True,capture_output=True,
                                     cwd=str(Path(__file__).resolve().parents[1]),timeout=30)
            except subprocess.TimeoutExpired:raise RULError('RUL inference timed out',504) from None
            except OSError:raise RULError('RUL worker unavailable',503) from None
            if p.returncode:raise RULError('RUL worker rejected input/model/signature',422)
            return json.loads(p.stdout)
        finally:self.lock.release()
    def close(self):
        self.closed=True
        return not self.lock.locked()
