"""单通道等长周期 CSV 的参考偏离与短期趋势；无训练、无后台线程。"""
from __future__ import annotations
import csv
import io
import math
import numpy as np
from pydantic import BaseModel,ConfigDict,Field
from .mechanism_core import FEATURES,cycle_features,reference_z,persistent_crossing,life_features

MAX_ROWS=20000


class MechanismBody(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    csv_text: str=Field(min_length=1,max_length=2*1024*1024)
    title: str=Field(default='',max_length=200)
    unit: str=Field(default='',max_length=40)
    reference_fraction: float=Field(default=.2,gt=0,le=.5)
    threshold: float=Field(default=6,gt=0,le=100)
    hold: int=Field(default=3,ge=1,le=100,strict=True)
    horizon: int=Field(default=5,ge=1,le=20,strict=True)


def import_cycles(text):
    if len(text.encode('utf8'))>2*1024*1024:raise ValueError('CSV限2MB UTF-8文本')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if reader.fieldnames!=['cycle','value']:raise ValueError('CSV表头必须为cycle,value')
    ids=[];groups=[];rows=0
    for row in reader:
        rows+=1
        if rows>MAX_ROWS or None in row or any(v is None for v in row.values()):
            raise ValueError('CSV限20000行且列数必须一致')
        try:
            raw=row['cycle'].strip()
            if not raw.isascii() or not raw.isdigit() or len(raw)>9:raise ValueError
            cycle=int(raw);value=float(row['value'])
            if not math.isfinite(value) or abs(value)>1e12:raise ValueError
        except ValueError:raise ValueError('cycle须为非负整数，value须为绝对值不超过1e12的有限数') from None
        if not ids or cycle!=ids[-1]:
            if ids and cycle!=ids[-1]+1:raise ValueError('周期须连续递增，同周期数据须相邻；不自动排序或填补')
            ids.append(cycle);groups.append([])
            if len(ids)>2000:raise ValueError('最多2000周期')
        groups[-1].append(value)
        if len(groups[-1])>2048:raise ValueError('每周期最多2048采样点')
    if len(ids)<24 or len(groups[0])<4 or len({len(g) for g in groups})!=1:
        raise ValueError('至少24个等长周期，每周期至少4点')
    return ids,np.asarray(groups,dtype=np.float64).T


def analyze(body: MechanismBody):
    ids,values=import_cycles(body.csv_text)
    features=cycle_features(values)
    z,usable,reference_count=reference_z(features,body.reference_fraction)
    if len(ids)-reference_count<body.hold:raise ValueError('参考期后周期数不足hold')
    result={'status':'unknown','method':'fixed_reference_cycle_deviation',
            'cycles':len(ids),'samples_per_cycle':values.shape[0], 'reference_count':reference_count,
            'reference_assumption':'first_cycles_declared_healthy_same_operating_condition',
            'threshold':body.threshold,'hold':body.hold,'feature_names':list(FEATURES),
            'active_features':[FEATURES[i] for i in np.flatnonzero(usable)],
            'inactive_features':[FEATURES[i] for i in np.flatnonzero(~usable)],
            'first_persistent_crossing_cycle':None,'trend':None,'forecast':[],
            'rul_cycles':None,'diagnosis':'reference_deviation_only',
            'review_required':True,'source':'weihu-MIT', 'series':[]}
    if not usable.any():
        result['reason']='reference_has_no_usable_variation'
        return result
    index=np.max(np.abs(z[:,usable]),axis=1)
    crossing,_=persistent_crossing(index,body.threshold,reference_count,body.hold)
    confirmed=bool(len(index)>=body.hold and np.all(index[-body.hold:]>body.threshold))
    result['status']='review' if confirmed else 'within_reference'
    result['first_persistent_crossing_cycle']=ids[crossing] if crossing is not None else None
    result['series']=[{'cycle':cycle,'reference':i<reference_count,'deviation':float(index[i]),
                       'features':dict(zip(FEATURES,map(float,features[i])))} for i,cycle in enumerate(ids)]
    # 仅末尾最多20个非参考周期的线性外推；不是寿命模型或置信区间。
    tail=index[max(reference_count,len(index)-20):]
    if len(tail)>=5 and body.horizon<=len(tail):
        summary=life_features(tail[None,:,None])[0]
        slope=float(summary[3]);mean=float(summary[1]);t=np.arange(len(tail),dtype=float)
        fitted=mean+slope*(t-t.mean())
        residual=float(np.sqrt(np.mean((tail-fitted)**2)))
        result['trend']={'slope_per_cycle':slope,'window_cycles':len(tail),'fit_rmse':residual,
                         'method':'least_squares_linear_extrapolation','validated_rul':False}
        result['forecast']=[{'cycle':ids[-1]+step,'deviation':max(0.,mean+slope*((len(tail)-1)/2+step))}
                            for step in range(1,body.horizon+1)]
    return result
