"""一次性 CPU 读码/OCR worker；输入由父进程清洗，stdout 仅结构化结果。"""
from __future__ import annotations
import importlib.util
import importlib.metadata
import json
from pathlib import Path
import sys


def capabilities():
    result = []
    for kind, package in [('barcode', 'zxingcpp'), ('ocr', 'rapidocr_onnxruntime')]:
        spec = importlib.util.find_spec(package)
        available = spec is not None
        models = []
        if kind == 'ocr' and spec:
            models = list((Path(spec.origin).parent / 'models').glob('*.onnx'))
            available = len(models) >= 3
        result.append({'id': kind, 'available': available,
                       'reason': '' if available else '可选依赖或随包ONNX权重未安装',
                       'runtime': 'zxing-cpp' if kind == 'barcode' else 'RapidOCR ONNX CPU',
                       'model_files': [p.name for p in models], 'validation': 'unverified'})
    return result


def read(spec):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(spec['image_path'],dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('invalid image')
    if spec['kind'] == 'barcode':
        import zxingcpp
        result=[]
        for code in zxingcpp.read_barcodes(image)[:64]:
            if not code.valid:
                continue
            pos=code.position
            points=[pos.top_left,pos.top_right,pos.bottom_right,pos.bottom_left]
            result.append({'text':code.text[:4096], 'format':str(code.format), 'confidence':None,
                           'quad':[[int(p.x),int(p.y)] for p in points]})
        return result
    if spec['kind'] == 'ocr':
        from rapidocr_onnxruntime import RapidOCR
        engine=RapidOCR()
        values,_=engine(image)
        return [{'text':str(text)[:4096], 'format':'text', 'confidence':float(score),
                 'quad':[[float(x),float(y)] for x,y in box]} for box,text,score in (values or [])[:64]]
    raise ValueError('unsupported reader')


if __name__=='__main__':
    try:
        spec=json.loads(sys.stdin.buffer.read(65536))
        result={'capabilities':capabilities()} if spec.get('action')=='capabilities' else {'items':read(spec)}
        print(json.dumps(result,ensure_ascii=True,allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__}))
        raise SystemExit(1)
