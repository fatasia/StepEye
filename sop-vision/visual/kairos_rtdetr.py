"""Explicit one-class Kairos RT-DETR adapter; no YOLO layout inference."""
from __future__ import annotations

import math
import numpy as np
from PIL import Image
from .detector_registry import Detection
from .detection_eval import iou


def _batch_one(shape):
    return len(shape)==3 and (shape[0]==1 or shape[0] is None or isinstance(shape[0],str))


def validate_native(inputs, outputs):
    """Validate native IO, allowing symbolic batch/query axes but always infer batch one."""
    if len(inputs)!=1:
        raise ValueError('Kairos requires one pixel_values input')
    inp=inputs[0]
    if (inp.name!='pixel_values' or inp.type!='tensor(float)' or len(inp.shape)!=4
            or inp.shape[1:]!=[3,640,640]
            or not (inp.shape[0]==1 or inp.shape[0] is None or isinstance(inp.shape[0],str))):
        raise ValueError('Kairos input must be float [batch,3,640,640]')
    by_name={o.name:o for o in outputs}
    if len(outputs)!=2 or set(by_name)!={'logits','pred_boxes'}:
        raise ValueError('Kairos requires logits and pred_boxes outputs')
    for name,width in [('logits',1),('pred_boxes',4)]:
        out=by_name[name]
        if out.type!='tensor(float)' or not _batch_one(out.shape) or out.shape[-1]!=width:
            raise ValueError('Invalid Kairos output shape/type')
        if isinstance(out.shape[1],int) and not 1<=out.shape[1]<=1000:
            raise ValueError('Kairos query count exceeds limits')


def preprocess(frame):
    if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
            or frame.shape[2]!=3 or not frame.size or frame.shape[0]*frame.shape[1]>20000000):
        raise ValueError('Expected nonempty uint8 BGR image up to 20M pixels')
    # Author preprocessor_config: resize directly (no letterbox), resample=2, /255, no normalize.
    rgb=Image.fromarray(frame[:,:,::-1]).resize((640,640),Image.Resampling.BILINEAR)
    return np.ascontiguousarray(np.asarray(rgb).transpose(2,0,1)[None],dtype=np.float32)/255.


def decode(logits,boxes,width,height,threshold=.49,max_results=500):
    if (logits.ndim!=3 or logits.shape[0]!=1 or logits.shape[2]!=1
            or boxes.shape!=(1,logits.shape[1],4) or not 1<=logits.shape[1]<=1000
            or not np.isfinite(logits).all() or not np.isfinite(boxes).all()):
        raise ValueError('Invalid Kairos tensor values/shapes')
    scores=1/(1+np.exp(-np.clip(logits[0,:,0],-80,80)))
    hits=[]
    for idx in np.argsort(-scores,kind='stable'):
        score=float(scores[idx])
        if score<threshold:break
        cx,cy,bw,bh=map(float,boxes[0,idx])
        if bw<=0 or bh<=0:continue
        box=(max(0.,min(float(width),(cx-bw/2)*width)),max(0.,min(float(height),(cy-bh/2)*height)),
             max(0.,min(float(width),(cx+bw/2)*width)),max(0.,min(float(height),(cy+bh/2)*height)))
        if box[0]>=box[2] or box[1]>=box[3]:continue
        if any(iou(box,h.xyxy)>.5 for h in hits):continue
        hits.append(Detection('water',box,score))
        if len(hits)>=max_results:break
    return hits


class KairosRTDetrBackend:
    adapter_name='kairos_rtdetr'
    input_size=640

    def __init__(self,model_path,classes,threshold=.49,max_results=500):
        import onnxruntime as ort
        if (list(classes)!=['water'] or isinstance(threshold,bool) or not math.isfinite(threshold)
                or not 0<threshold<=1 or type(max_results)!=int or not 1<=max_results<=1000):
            raise ValueError('Kairos supports only water and valid threshold/result limit')
        self.session=None
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        session=ort.InferenceSession(str(model_path),sess_options=options,providers=['CPUExecutionProvider'])
        validate_native(session.get_inputs(),session.get_outputs())
        self.session=session;self.classes=['water'];self.threshold=threshold;self.max_results=max_results
        self.input_name='pixel_values'

    def detect(self,frame):
        if self.session is None:raise RuntimeError('Detector is closed')
        tensor=preprocess(frame)
        logits,boxes=self.session.run(['logits','pred_boxes'],{'pixel_values':tensor})
        return decode(logits,boxes,frame.shape[1],frame.shape[0],self.threshold,self.max_results)

    def close(self):
        self.session=None
