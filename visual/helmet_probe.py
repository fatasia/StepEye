"""同步、有界的头部 ROI 检测底层；实时调用方必须放入最新帧异步 worker。"""
from __future__ import annotations
import ast
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from pydantic import BaseModel,ConfigDict,Field
from .detector_registry import YoloOnnxBackend
from .tracking import ObjectTrack,iou

CLASSES=['With Helmet','Without Helmet']
HELMET_SHA='fa30e26e86c6cc1a1c78678aca272b4ce06bba85b38e3d08cceb1e590c0cc5ab'


class HelmetProbeCfg(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    enabled: bool=False
    model: str='models/community/helmet.onnx'
    sha256: str=Field(default=HELMET_SHA,pattern=r'^[0-9a-f]{64}$')
    max_rois: int=Field(default=2,ge=1,le=4)
    interval_s: float=Field(default=.2,ge=.1,le=10)
    idle_s: float=Field(default=10,ge=1,le=300)
    min_person_height: float=Field(default=80,ge=40,le=4096)
    min_confidence: float=Field(default=.5,ge=.05,le=.95)


@dataclass(frozen=True)
class HelmetObservation(ObjectTrack):
    parent_track_id: int=0


class HelmetROIProbe:
    """不启动线程；连续采样复用 session，调用 process/tick/close 时回收空闲资源。"""
    def __init__(self,cfg:HelmetProbeCfg,base_dir=None,*,backend_factory=YoloOnnxBackend,clock=time.monotonic):
        self.cfg=cfg;self.path=Path(cfg.model)
        if not self.path.is_absolute() and base_dir is not None:self.path=Path(base_dir)/self.path
        self._factory,self._clock=backend_factory,clock
        self._backend=None;self._last_use=None;self._last_ts=None;self._cursor=0;self._retry_at=0.

    def _open(self):
        if self._backend is not None:return
        h=hashlib.sha256()
        with self.path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=self.cfg.sha256:raise ValueError('helmet model hash mismatch')
        backend=self._factory(self.path,CLASSES,self.cfg.min_confidence,4)
        try:
            names=ast.literal_eval(backend.session.get_modelmeta().custom_metadata_map.get('names','{}'))
            if names!={0:'With Helmet',1:'Without Helmet'}:raise ValueError('helmet class map mismatch')
            if backend.input_size!=640 or backend.session.get_outputs()[0].shape!=[1,6,8400]:
                raise ValueError('helmet shape mismatch')
        except Exception:backend.close();raise
        self._backend=backend

    def tick(self):
        if self._backend is not None and self._last_use is not None and self._clock()-self._last_use>=self.cfg.idle_s:
            self._backend.close();self._backend=None

    def close(self):
        if self._backend is not None:self._backend.close()
        self._backend=None;self._last_use=None;self._last_ts=None;self._cursor=0;self._retry_at=0.

    reset_observations=close

    def process(self,frame,ts,tracks):
        import math
        self.tick()
        result={'frame_ts':ts,'observations':[],'sampled_person_ids':[],'status':'disabled'}
        if not self.cfg.enabled:return result
        if not math.isfinite(ts):raise ValueError('invalid frame timestamp')
        if self._last_ts is not None:
            if ts<self._last_ts:self.close()
            elif ts-self._last_ts<self.cfg.interval_s:
                result['status']='not_sampled';return result
        if self._clock()<self._retry_at:result['status']='cooldown';return result
        people=sorted([p for p in tracks if p.label=='person' and p.last_seen==ts
                       and p.confidence>=self.cfg.min_confidence
                       and p.xyxy[3]-p.xyxy[1]>=self.cfg.min_person_height],key=lambda p:p.track_id)[:64]
        if not people:result['status']='no_person';return result
        h,w=frame.shape[:2];candidates=[]
        for person in people:
            x0,y0,x1,y1=person.xyxy;pw,ph=x1-x0,y1-y0
            box=(max(0,int(x0-pw*.05)),max(0,int(y0-ph*.05)),min(w,int(x1+pw*.05)),min(h,int(y0+ph*.35)))
            if min(box[2]-box[0],box[3]-box[1])>=16:candidates.append((person,box))
        # 重叠头部不能可靠归属，跳过而不是借 parent ID 强行归属。
        candidates=[(p,b) for p,b in candidates if not any(q.track_id!=p.track_id and iou(b,c)>.3 for q,c in candidates)]
        if not candidates:result['status']='ambiguous_or_small';return result
        offset=self._cursor%len(candidates)
        selected=(candidates[offset:]+candidates[:offset])[:self.cfg.max_rois]
        self._cursor=(offset+len(selected))%len(candidates)
        self._last_ts=ts
        try:
            self._open()
            for person,(x0,y0,x1,y1) in selected:
                hits=self._backend.detect(frame[y0:y1,x0:x1])
                result['sampled_person_ids'].append(person.track_id)
                for index,hit in enumerate(hits):
                    cls=CLASSES.index(hit.label)
                    box=(hit.xyxy[0]+x0,hit.xyxy[1]+y0,hit.xyxy[2]+x0,hit.xyxy[3]+y0)
                    # 负 ID 命名空间与主检测器正 ID 分离；仅为证据 ID，不是新的人体轨迹。
                    result['observations'].append(HelmetObservation(-(person.track_id*16+cls*4+index+1),
                        hit.label,box,hit.confidence,ts,ts,1,parent_track_id=person.track_id))
            self._last_use=self._clock();result['status']='sampled'
        except Exception:
            self.close();self._retry_at=self._clock()+5
            result.update(status='unavailable',observations=[],sampled_person_ids=[])
        return result
