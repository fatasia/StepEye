"""Inspect a local public ONNX and run one CPU adapter probe; never executes repo code."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time


def inspect_onnx(path: Path) -> dict:
    import onnx
    path=Path(path)
    if path.suffix.lower() != '.onnx' or not 0 < path.stat().st_size <= 128*1024*1024:
        raise ValueError('Expected bounded local ONNX')
    model=onnx.load(str(path),load_external_data=False)
    def check_graph(graph):
        for tensor in graph.initializer:
            if tensor.data_location == onnx.TensorProto.EXTERNAL or tensor.external_data:
                raise ValueError('External tensor data not accepted')
        for node in graph.node:
            if node.domain not in ('','ai.onnx'):
                raise ValueError('Custom operator domain not accepted')
            for attr in node.attribute:
                if attr.type == onnx.AttributeProto.GRAPH: check_graph(attr.g)
                for nested in attr.graphs: check_graph(nested)
    check_graph(model.graph)
    onnx.checker.check_model(model)
    def spec(value):
        tensor=value.type.tensor_type
        return {'name':value.name,'dtype':onnx.TensorProto.DataType.Name(tensor.elem_type),
                'shape':[d.dim_value if d.HasField('dim_value') else d.dim_param for d in tensor.shape.dim]}
    metadata={v.key:v.value for v in model.metadata_props}
    raw=metadata.get('names','')
    names=[]
    if raw:
        if len(raw)>64*1024: raise ValueError('Class metadata oversized')
        value=ast.literal_eval(raw)
        if isinstance(value,dict) and set(value)==set(range(len(value))): value=[value[i] for i in range(len(value))]
        if not isinstance(value,list) or not all(isinstance(n,str) and 0<len(n)<=100 for n in value):
            raise ValueError('Invalid class metadata')
        names=value
    inputs=[spec(v) for v in model.graph.input]; outputs=[spec(v) for v in model.graph.output]
    compatible=(len(inputs)==1 and inputs[0]['dtype']=='FLOAT' and len(inputs[0]['shape'])==4 and inputs[0]['shape'][:2]==[1,3]
                and type(inputs[0]['shape'][2]) is int and inputs[0]['shape'][2]==inputs[0]['shape'][3]
                and 128<=inputs[0]['shape'][2]<=1280 and inputs[0]['shape'][2]%32==0
                and len(outputs)==1 and len(outputs[0]['shape'])==3 and outputs[0]['shape'][0]==1
                and any(d in (4+len(names),5+len(names)) for d in outputs[0]['shape'][1:]) and bool(names))
    return {'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'classes':names,'inputs':inputs,'outputs':outputs,'opsets':[{ 'domain':x.domain,'version':x.version} for x in model.opset_import],
            'task':metadata.get('task','unknown'),'adapter_compatible':compatible}


def probe(path: Path,image: Path,threshold:float=.25,timeout:float=30) -> dict:
    if not math.isfinite(threshold) or not 0.05 <= threshold <= .95:
        raise ValueError('threshold must be 0.05..0.95')
    if not image.is_file() or not 0 < image.stat().st_size <= 20*1024*1024:
        raise ValueError('image must be a bounded local file')
    spec=inspect_onnx(path)
    shape=spec['inputs'][0]['shape'] if len(spec['inputs'])==1 else []
    if (len(shape)!=4 or shape[:2]!=[1,3] or not isinstance(shape[2],int)
            or shape[2]!=shape[3] or not 128<=shape[2]<=1280 or not spec['classes']):
        raise ValueError('Unsupported native static shape')
    request={'model_path':str(path.resolve()),'image_path':str(image.resolve()),'classes':spec['classes'],
             'project_classes':spec['classes'],'threshold':threshold,'native_size':shape[2]}
    started=time.monotonic()
    child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--native-worker'],input=json.dumps(request).encode(),
        cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=timeout,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if child.returncode or len(child.stdout)>256*1024: raise ValueError('Native inference failed')
    result=json.loads(child.stdout)
    return {**spec,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
            'threshold':threshold,'targets':result['targets'],'elapsed_ms':round((time.monotonic()-started)*1000,2),
            'provider':'CPUExecutionProvider','validation':'single_image_smoke_only'}


def native_worker():
    # Native-size verification only: the production adapter also validates static square inputs.
    import numpy as np
    import cv2
    import onnxruntime as ort
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from visual.onnx_detector import letterbox,postprocess_detections
    request=json.loads(sys.stdin.buffer.read(64*1024))
    frame=cv2.imdecode(np.fromfile(request['image_path'],dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None: raise ValueError('invalid image')
    blob,r,pad=letterbox(frame,request['native_size'])
    tensor=blob[:,:,::-1].transpose(2,0,1)[None].astype(np.float32)/255
    options=ort.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
    session=ort.InferenceSession(request['model_path'],sess_options=options,providers=['CPUExecutionProvider'])
    out=session.run(None,{session.get_inputs()[0].name:tensor})
    if len(out)!=1: raise ValueError('multiple outputs')
    hits=postprocess_detections(out[0],request['threshold'],request['classes'],frame.shape[:2],r,pad)
    h,w=frame.shape[:2]
    targets=[{'label_id':request['classes'].index(name),'confidence':score,
              'xyxy_norm':[float(box[0]/w),float(box[1]/h),float(box[2]/w),float(box[3]/h)]} for name,box,score in hits]
    print(json.dumps({'targets':targets},allow_nan=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True); p.add_argument('--image',type=Path)
    p.add_argument('--out',type=Path,required=True); p.add_argument('--threshold',type=float,default=.25)
    a=p.parse_args()
    result=probe(a.model,a.image,a.threshold) if a.image else inspect_onnx(a.model)
    with a.out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':
    if sys.argv[1:]==['--native-worker']: native_worker()
    else: main()
