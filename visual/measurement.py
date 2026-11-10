"""限定任务：两个 ROI 内各一条近水平/竖直强边缘的平面垂直间距。"""
from __future__ import annotations
import math
from typing import Literal
import cv2
import numpy as np
from pydantic import BaseModel,ConfigDict,Field


class EdgeMeasureSpec(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    scope_id: str=Field(min_length=1,max_length=160)
    reference: str=Field(min_length=1,max_length=200)
    rois: tuple[tuple[int,int,int,int],tuple[int,int,int,int]]
    edge_direction: Literal['vertical','horizontal']


class ReviewNeeded(Exception):pass


def extract_edge(gray,roi,direction):
    x0,y0,x1,y1=roi;crop=gray[y0:y1,x0:x1]
    horizontal=direction=='horizontal'
    work=crop.T if horizontal else crop
    if min(work.shape)<12:raise ReviewNeeded('roi_too_small')
    gradient=np.abs(cv2.Sobel(cv2.GaussianBlur(work,(3,3),0),cv2.CV_32F,1,0,ksize=3))
    points=[];ambiguous=0
    for row in range(2,work.shape[0]-2):
        strength=gradient[row].copy();strength[:3]=0;strength[-3:]=0
        peak=int(strength.argmax());maximum=float(strength[peak])
        if maximum<40:continue
        others=strength.copy();others[max(0,peak-3):peak+4]=0
        if float(others.max())>maximum*.65:ambiguous+=1;continue
        lo,hi=max(0,peak-2),min(len(strength),peak+3)
        center=float(np.average(np.arange(lo,hi),weights=strength[lo:hi]))
        points.append((center,row))
    count=work.shape[0]-4
    if ambiguous>count*.2:raise ReviewNeeded('multiple_edges')
    if len(points)<count*.7:raise ReviewNeeded('weak_or_incomplete_edge')
    values=np.asarray(points,np.float32)
    vx,vy,cx,cy=map(float,cv2.fitLine(values,cv2.DIST_HUBER,0,.01,.01).ravel())
    if abs(vy)<.01 or abs(vx/vy)>math.tan(math.radians(10)):raise ReviewNeeded('edge_direction_mismatch')
    slope=vx/vy;offset=cx-slope*cy
    residual=np.abs(values[:,0]-(slope*values[:,1]+offset))
    if float(np.percentile(residual,95))>2:raise ReviewNeeded('edge_not_straight')
    line=np.array([[slope*2+offset,2],[slope*(work.shape[0]-3)+offset,work.shape[0]-3]],dtype=float)
    if horizontal:line=line[:,::-1];values=values[:,::-1]
    line += [x0,y0];values += [x0,y0]
    return {'line':line.tolist(),'points':values[::max(1,len(values)//40)].tolist(),
            'residual_p95_px':float(np.percentile(residual,95)),'support':len(points)/count}


def measure_edges(store,calibration_id,image,spec):
    spec=EdgeMeasureSpec.model_validate(spec)
    h,w=image.shape[:2]
    if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3:raise ValueError('需要uint8 RGB图像')
    if w*h>4_000_000 or max(w,h)>4096:raise ValueError('图像最多400万像素、边长4096')
    for x0,y0,x1,y1 in spec.rois:
        if not (0<=x0<x1<=w and 0<=y0<y1<=h):raise ValueError('ROI超出图片或退化')
    common=dict(scope_id=spec.scope_id,reference=spec.reference,image_size=(w,h))
    record=store.get(calibration_id)
    # 在提取前也检查身份/标定有效性，不能把无效标定伪装成普通待复核。
    store.measure(calibration_id,{**common,'points':[record['coverage'][0],record['coverage'][1]]})
    result={'status':'review','reason':None,'distance':None,'calibration_id':calibration_id,
            'version':record['version'],'unit':record['unit'],'planar_only':True,'edges':[]}
    try:
        gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
        edges=result['edges']
        for roi in spec.rois:edges.append(extract_edge(gray,roi,spec.edge_direction))
        physical=[]
        for edge in edges:
            mapped=store.measure(calibration_id,{**common,'points':edge['line']})
            physical.append(np.array(mapped['physical_points'],float))
        a,b=physical;u=a[1]-a[0];v=b[1]-b[0]
        if min(np.linalg.norm(u),np.linalg.norm(v))<1e-9:raise ReviewNeeded('degenerate_physical_line')
        cos=abs(float(np.dot(u,v)/(np.linalg.norm(u)*np.linalg.norm(v))))
        if cos<math.cos(math.radians(3)):raise ReviewNeeded('non_parallel_edges')
        # 取第一条支持线段中心，垂直投影至第二条物理线段，禁止无证据延长线测量。
        start=a.mean(axis=0);t=float(np.dot(start-b[0],v)/np.dot(v,v))
        if not 0<=t<=1:raise ReviewNeeded('no_common_edge_span')
        end=b[0]+t*v
        physical_points=np.array([[start,end]],np.float64)
        pixels=cv2.perspectiveTransform(physical_points,np.linalg.inv(np.array(record['matrix'],float)))[0]
        measured=store.measure(calibration_id,{**common,'points':pixels.tolist()})
        if measured['distance']<=1e-9:raise ReviewNeeded('coincident_edges')
        result.update(measured,status='measured',reason=None,measurement_line=pixels.tolist(),
                      method='two_roi_edges_planar_perpendicular',review_required=True)
    except ReviewNeeded as exc:result['reason']=str(exc)
    return result
