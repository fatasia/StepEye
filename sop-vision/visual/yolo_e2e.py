"""显式 YOLO 端到端单图 ONNX 适配：[1,N,6] xyxy/conf/class_id，不再执行NMS。"""
from __future__ import annotations

import ast

import numpy as np

from .detector_registry import Detection, YoloOnnxBackend
from .onnx_detector import letterbox


class YoloE2EOnnxBackend(YoloOnnxBackend):
    adapter_name = "yolo_e2e"
    def __init__(self, model_path, classes, threshold=.5, max_results=500):
        if (any(not isinstance(c,str) or not c.strip() or c!=c.strip() for c in classes)
                or len(set(classes))!=len(classes) or len(classes)>1000):
            raise ValueError("端到端模型需要完整且不重复的类别表")
        super().__init__(model_path,classes,threshold,max_results)
        try:
            output=self.session.get_outputs()[0]
            if (output.type!="tensor(float)" or len(output.shape)!=3 or output.shape[0]!=1
                    or type(output.shape[1]) is not int or not 1<=output.shape[1]<=1000 or output.shape[2]!=6):
                raise ValueError("端到端适配器只支持静态float32 [1,N,6]，N为1–1000")
            metadata=self.session.get_modelmeta().custom_metadata_map
            if "end2end" in metadata and metadata["end2end"].lower()!="true":
                raise ValueError("模型元数据未声明端到端输出")
            if "names" in metadata:
                if len(metadata["names"])>65536: raise ValueError("模型类别元数据过大")
                declared=ast.literal_eval(metadata["names"])
                if isinstance(declared,dict):
                    if set(declared)!=set(range(len(classes))): raise ValueError("模型类别编号不连续")
                    declared=[declared[i] for i in range(len(classes))]
                if declared!=list(classes): raise ValueError("登记类别顺序与模型元数据不匹配")
        except Exception:
            self.close()
            raise

    def detect(self, frame):
        if (not isinstance(frame,np.ndarray) or frame.dtype!=np.uint8 or frame.ndim!=3
                or frame.shape[2]!=3 or not frame.size):
            raise ValueError("检测输入须为非空uint8 BGR图像")
        if self.session is None: raise RuntimeError("检测器已关闭")
        blob,r,pad=letterbox(frame,self.input_size)
        tensor=np.ascontiguousarray(blob[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255
        prediction=self.session.run(None,{self.input_name:tensor})[0]
        if prediction.ndim!=3 or prediction.shape[0]!=1 or prediction.shape[2]!=6 or not np.isfinite(prediction).all():
            raise ValueError("端到端模型返回非法形状或非有限数值")
        rows=prediction[0]
        if np.any((rows[:,4]<0)|(rows[:,4]>1)):
            raise ValueError("端到端输出置信度不在0–1")
        rows=rows[rows[:,4]>=self.threshold]
        h,w=frame.shape[:2];ox,oy=pad;out=[]
        for row in rows[np.argsort(-rows[:,4],kind="stable")]:
            cid=float(row[5])
            if not cid.is_integer() or not 0<=cid<len(self.classes):
                raise ValueError("端到端输出含非法类别ID")
            x0,y0,x1,y1=(float(v) for v in row[:4])
            if x0>=x1 or y0>=y1: continue
            box=(max(0.,(x0-ox)/r),max(0.,(y0-oy)/r),min(float(w),(x1-ox)/r),min(float(h),(y1-oy)/r))
            if box[0]>=box[2] or box[1]>=box[3]: continue
            out.append(Detection(self.classes[int(cid)],box,float(row[4])))
            if len(out)>=self.max_results: break
        return out
