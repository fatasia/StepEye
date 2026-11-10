"""Offline actuator residual evidence against explicitly reviewed healthy windows."""
import hashlib,importlib.util,threading
from typing import Annotated,Literal
import numpy as np
from pydantic import BaseModel,ConfigDict,Field,model_validator

CHANNELS=['command_position_mm','following_error_mm','current_A']
Number=Annotated[float,Field(strict=True,allow_inf_nan=False,ge=-1000000,le=1000000)]
Identity=Annotated[str,Field(min_length=1,max_length=100)]

class ActuatorWindow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:Identity
    source_group:Identity
    context:Identity
    values:list[tuple[Number,Number,Number]]=Field(min_length=400,max_length=400)

class ActuatorBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(default='',max_length=200)
    unit:Literal['mm,A']='mm,A'
    sample_rate_hz:Literal[25]=25
    channels:list[str]=Field(min_length=3,max_length=3)
    reference_confirmed_healthy:Literal[True]
    reference_source:str=Field(min_length=1,max_length=300)
    reference_windows:list[ActuatorWindow]=Field(min_length=1,max_length=10)
    evaluation_windows:list[ActuatorWindow]=Field(min_length=1,max_length=20)
    @model_validator(mode='after')
    def isolation(self):
        if self.channels!=CHANNELS:raise ValueError('Channel order must be command/error/current')
        all_windows=self.reference_windows+self.evaluation_windows
        if len({w.id for w in all_windows})!=len(all_windows):raise ValueError('Window IDs must be unique')
        if len({w.context for w in all_windows})!=1:raise ValueError('Reference and evaluation must share one operating context')
        if {w.source_group for w in self.reference_windows}&{w.source_group for w in self.evaluation_windows}:
            raise ValueError('Reference/evaluation source groups must not overlap')
        refs={hashlib.sha256(np.asarray(w.values,dtype=np.float64).tobytes()).hexdigest() for w in self.reference_windows}
        if any(hashlib.sha256(np.asarray(w.values,dtype=np.float64).tobytes()).hexdigest() in refs for w in self.evaluation_windows):
            raise ValueError('Evaluation reuses reference samples')
        return self

class ActuatorService:
    def __init__(self):self._lock=threading.Lock();self._closed=False
    def capabilities(self):
        return {'available':not self._closed and importlib.util.find_spec('scipy') is not None,
                'method':'actuator_motion_residual','channels':CHANNELS,'unit':'mm,A','sample_rate_hz':25,
                'window_samples':400,'max_reference_windows':10,'max_evaluation_windows':20,
                'supports_rul':False,'supports_fault_root_cause':False,'production_approval':False}
    def close(self):self._closed=True;return not self._lock.locked()
    def analyze(self,body):
        body=body if isinstance(body,ActuatorBody) else ActuatorBody.model_validate(body)
        if self._closed:raise RuntimeError('Actuator service closed')
        if not self._lock.acquire(False):raise RuntimeError('Actuator analysis busy')
        try:
            from .mechanism_actuator_core import motion_basis,fit_motion_reference,motion_residual
            refs=np.asarray([w.values for w in body.reference_windows],np.float64)
            if np.ptp(refs[:,:,0])<1e-6:raise ValueError('Healthy reference has no command motion')
            basis=motion_basis(refs[:,:,0]);reference=fit_motion_reference(basis,refs[:,:,1:],np.arange(len(refs)))
            rows=[]
            for window in body.evaluation_windows:
                values=np.asarray(window.values,np.float64)[None]
                if values[:,:,0].min()<refs[:,:,0].min()-1e-6 or values[:,:,0].max()>refs[:,:,0].max()+1e-6:
                    rows.append({'id':window.id,'source_group':window.source_group,'context':window.context,
                                 'status':'unknown','reason':'command_position_outside_reference_range'});continue
                residual=motion_residual(motion_basis(values[:,:,0]),values[:,:,1:],reference).astype(np.float64)
                rms=np.sqrt(np.mean(residual**2,axis=(0,1)))
                rows.append({'id':window.id,'source_group':window.source_group,'context':window.context,
                             'status':'evidence_only','error_residual_rms_mm':float(rms[0]),'current_residual_rms_A':float(rms[1]),
                             'standardized_rms':(rms/np.asarray(reference['reference_rms'])).tolist()})
            return {'status':'evidence_only','method':'actuator_motion_residual','unit':body.unit,
                    'reference':{'source':body.reference_source,'windows':len(refs),'rms':reference['reference_rms']},
                    'results':rows,'supports_rul':False,
                    'scope':'Offline healthy motion regression residual; no fault label, physical parameter identification or RUL'}
        finally:self._lock.release()
