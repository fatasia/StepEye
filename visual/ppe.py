"""逐人 PPE 区域关联：正类只记 observed，缺失仅接受明确负类持续证据。"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .tracking import iou
from .helmet_probe import HelmetProbeCfg

ALIASES = {
    'helmet': ('helmet', False), 'with helmet': ('helmet', False),
    'hardhat': ('helmet', False), 'hard hat': ('helmet', False),
    'without helmet': ('helmet', True), 'no-helmet': ('helmet', True),
    'no-hardhat': ('helmet', True), 'no hardhat': ('helmet', True),
    'safety vest': ('vest', False), 'vest': ('vest', False),
    'no-safety vest': ('vest', True), 'no-safety-vest': ('vest', True),
    'no vest': ('vest', True), 'without vest': ('vest', True),
    'gloves': ('gloves', False), 'glove': ('gloves', False),
    'no-gloves': ('gloves', True), 'no gloves': ('gloves', True),
    'without gloves': ('gloves', True),
    'goggles': ('goggles', False), 'safety goggles': ('goggles', False),
    'no-goggles': ('goggles', True), 'no goggles': ('goggles', True),
    'without goggles': ('goggles', True),
    'safety shoes': ('shoes', False), 'safety shoe': ('shoes', False),
    'safety_shoe': ('shoes', False), 'safety_shoes': ('shoes', False),
    'no-safety shoes': ('shoes', True), 'no-safety-shoes': ('shoes', True),
    'no-shoes': ('shoes', True), 'no shoes': ('shoes', True),
    'without safety shoes': ('shoes', True),
}
GEAR = ('helmet', 'vest', 'gloves', 'goggles', 'shoes')
# Relative image regions are association gates, not anatomical/ESD measurements.
# Bare-eye/hand/foot boxes are much smaller than the complete head/torso region.
NEGATIVE_COVERAGE = {'helmet': .4, 'vest': .4, 'gloves': .005, 'goggles': .08, 'shoes': .02}


class PPECfg(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    enabled: bool=False
    helmet_probe: HelmetProbeCfg=Field(default_factory=HelmetProbeCfg)
    zones: list[str]=Field(default_factory=list,max_length=64)
    min_confidence: float=Field(default=.5,ge=0,le=1)
    min_person_height: float=Field(default=80,ge=20,le=4096)
    min_frames: int=Field(default=3,ge=2,le=10000)
    min_s: float=Field(default=1.,ge=0,le=3600)
    max_gap_s: float=Field(default=.5,gt=0,le=10)
    gloves_required: int=Field(default=2,ge=1,le=2)
    shoes_required: int=Field(default=2,ge=1,le=2)
    severity: Literal['warn','error']='warn'


def intersection(a,b):
    return max(0.,min(a[2],b[2])-max(a[0],b[0]))*max(0.,min(a[3],b[3])-max(a[1],b[1]))


def area(box):return max(0.,box[2]-box[0])*max(0.,box[3]-box[1])


def region(person,gear):
    x0,y0,x1,y1=person.xyxy;w,h=x1-x0,y1-y0
    if gear=='helmet':return (x0,y0,x1,y0+h*.3)
    if gear=='vest':return (x0+w*.1,y0+h*.2,x1-w*.1,y0+h*.72)
    if gear=='goggles':return (x0+w*.1,y0+h*.02,x1-w*.1,y0+h*.22)
    if gear=='shoes':return (x0,y0+h*.7,x1,y1)
    return (x0,y0+h*.25,x1,y0+h*.9)


class PPEMonitor:
    def __init__(self,cfg:PPECfg,geoms):
        self.cfg,self.geoms=cfg,geoms
        if any(z not in geoms for z in cfg.zones):raise ValueError('PPE zone不存在')
        self.reset()

    def reset(self):
        self._last_ts=None;self._holds={};self._fired=set();self._rows=[]
        self._status='unknown'

    def snapshot(self):
        import copy
        return {'enabled':self.cfg.enabled,'status':self._status,'persons':copy.deepcopy(self._rows),
                'gloves_required':self.cfg.gloves_required,'shoes_required':self.cfg.shoes_required}

    def process(self,tracks,ts,*,available=True):
        if not self.cfg.enabled or not available or not math.isfinite(ts):self.reset();return []
        if self._last_ts is not None:
            if ts==self._last_ts:return []
            if ts<self._last_ts or ts-self._last_ts>self.cfg.max_gap_s:self.reset()
        self._last_ts=ts
        fresh=[t for t in tracks if t.last_seen==ts and t.confidence>=self.cfg.min_confidence]
        people=sorted([t for t in fresh if t.label.casefold()=='person'],key=lambda t:t.track_id)[:64]
        assignments={(p.track_id,g):{False:[],True:[]} for p in people for g in GEAR}
        ambiguous=set()
        for item in fresh[:1000]:
            alias=ALIASES.get(item.label.casefold().strip())
            if alias is None:continue
            gear,negative=alias
            if item.xyxy[2]-item.xyxy[0]<4 or item.xyxy[3]-item.xyxy[1]<4:continue
            candidates=[]
            for person in people:
                parent_id=getattr(item,'parent_track_id',None)
                if parent_id is not None and parent_id!=person.track_id:continue
                if person.xyxy[3]-person.xyxy[1]<self.cfg.min_person_height:continue
                part=region(person,gear);overlap=intersection(item.xyxy,part)
                cover=overlap/area(item.xyxy) if area(item.xyxy) else 0.
                # 显式负类还需足够区域证据；手/眼/脚不能套用整头40%的面积门槛。
                if cover>=.7 and (not negative or overlap/area(part)>=NEGATIVE_COVERAGE[gear]):
                    candidates.append((cover,person.track_id))
            candidates.sort(reverse=True)
            if len(candidates)>1 and candidates[0][0]-candidates[1][0]<.15:
                ambiguous.update((pid,gear) for _,pid in candidates)
                continue
            if candidates:
                assignments[(candidates[0][1],gear)][negative].append(item)
        active=set();alerts=[];rows=[]
        for person in people:
            zones=[z for z,g in self.geoms.items() if (not self.cfg.zones or z in self.cfg.zones)
                   and g.contains((person.xyxy[0]+person.xyxy[2])/2,person.xyxy[3])]
            if not zones:continue
            row={'track_id':person.track_id,'observed_at':ts,'zones':zones,'gear':{}}
            for gear in GEAR:
                key=(person.track_id,gear);assigned=assignments[key]
                positives=[]
                for item in sorted(assigned[False],key=lambda t:-t.confidence):
                    if all(iou(item.xyxy,prev.xyxy)<.5 for prev in positives):positives.append(item)
                negatives=assigned[True]
                status='unknown';reason='no_evidence';evidence=[]
                if person.xyxy[3]-person.xyxy[1]<self.cfg.min_person_height:reason='person_too_small'
                elif key in ambiguous:reason='ambiguous_person'
                elif positives and negatives:reason='conflicting_evidence'
                elif negatives:
                    active.add(key)
                    since,count=self._holds.get(key,(ts,0));count+=1
                    self._holds[key]=(since,count)
                    evidence=[t.track_id for t in negatives]
                    status='missing' if count>=self.cfg.min_frames and ts-since>=self.cfg.min_s else 'pending'
                    reason='explicit_negative'
                    if status=='missing' and key not in self._fired:
                        self._fired.add(key)
                        alerts.append({'rule':f'ppe-{gear}','name':f'PPE {gear}','kind':'ppe_missing',
                            'zone':zones[0],'severity':self.cfg.severity,'ts':ts,'duration_s':round(ts-since,3),
                            'track_ids':[person.track_id],'gear':gear,'evidence_track_ids':evidence,
                            'source':'ppe_tracks','message':f'{gear}缺失持续观测'})
                elif positives:
                    evidence=[t.track_id for t in positives]
                    required={'gloves':self.cfg.gloves_required,'shoes':self.cfg.shoes_required}.get(gear,1)
                    distinct=True
                    if gear in ('gloves','shoes') and required==2 and len(positives)>=2:
                        centers=[(t.xyxy[0]+t.xyxy[2])/2 for t in positives]
                        distinct=max(centers)-min(centers)>=(person.xyxy[2]-person.xyxy[0])*.15
                    status='observed' if len(positives)>=required and distinct else 'unknown'
                    reason='region_association' if status=='observed' else 'insufficient_count'
                row['gear'][gear]={'status':status,'reason':reason,'evidence_track_ids':evidence}
            rows.append(row)
        self._holds={k:v for k,v in self._holds.items() if k in active}
        self._fired.intersection_update(active)
        self._rows=rows;self._status='observed' if rows else 'no_person_observed'
        return alerts
