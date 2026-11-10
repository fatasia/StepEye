"""图像读码入口：文件清洗、ROI、有界一次性进程，不执行识别出的内容。"""
from __future__ import annotations
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from fastapi import APIRouter, File, Form, UploadFile
from PIL import Image
from .labels import LabelError
from .model_routes import ModelTrials
from .llm_provider import load_env_file, _default_env_path


class ReadingService:
    def __init__(self, python=None, timeout_s=20, env_file=None):
        values = load_env_file(Path(env_file) if env_file else _default_env_path())
        self.python = (python or os.environ.get('STEPEYE_READER_PYTHON')
                       or values.get('STEPEYE_READER_PYTHON') or sys.executable)
        self.timeout_s = timeout_s
        self._slot = threading.Lock()

    def _worker(self,spec):
        with tempfile.TemporaryFile() as output:
            try:
                proc=subprocess.Popen([self.python,'-m','visual.code_reader'],
                    cwd=Path(__file__).resolve().parents[1],stdin=subprocess.PIPE,stdout=output,
                    stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except OSError:
                raise LabelError('读码运行环境不可用',503) from None
            try:
                proc.communicate(json.dumps(spec).encode(),timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise LabelError('读码超时，进程已停止',504) from None
            finally:
                if proc.poll() is None:proc.kill()
                proc.communicate()
            output.seek(0);raw=output.read(1024*1024+1)
            if len(raw)>1024*1024:raise LabelError('读码输出过大',422)
            try:result=json.loads(raw)
            except (ValueError,UnicodeError):raise LabelError('读码返回无效结果',422) from None
            if not isinstance(result,dict):raise LabelError('读码返回无效结果',422)
            if proc.returncode or 'error' in result:
                raise LabelError('读码失败，请检查可选依赖与ONNX权重',503)
            return result

    def catalog(self):
        if not self._slot.acquire(blocking=False):raise LabelError('已有读码任务',429)
        try:return self._worker({'action':'capabilities'})['capabilities']
        finally:self._slot.release()

    def read(self,upload,kind='barcode',roi=None):
        if kind not in ('barcode','ocr'):raise LabelError('不支持的读码类型')
        if roi is not None:
            if (not isinstance(roi,list) or len(roi)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in roi)
                    or not (0<=roi[0]<roi[2]<=1 and 0<=roi[1]<roi[3]<=1)):
                raise LabelError('ROI须为归一化[x0,y0,x1,y1]')
        if not self._slot.acquire(blocking=False):raise LabelError('已有读码任务',429)
        started=time.monotonic()
        try:
            raw=upload.read(10*1024*1024+1)
            if not raw or len(raw)>10*1024*1024:raise LabelError('图片不能为空且最多10MB',413)
            try:
                with Image.open(io.BytesIO(raw)) as header:
                    if header.width*header.height>4_000_000 or max(header.size)>4096:
                        raise LabelError('图片最多400万像素，单边最多4096',413)
            except (OSError,ValueError,Image.DecompressionBombError):
                raise LabelError('图片无法解码') from None
            with tempfile.TemporaryDirectory(prefix='stepeye-reader-') as folder:
                path=Path(folder)/'image.png'
                width,height=ModelTrials._image(raw,path)
                x0,y0,x1,y1=0,0,width,height
                if roi is not None:
                    x0,y0=int(roi[0]*width),int(roi[1]*height)
                    x1,y1=int(roi[2]*width),int(roi[3]*height)
                    if x0>=x1 or y0>=y1:raise LabelError('ROI像素范围为空')
                    with Image.open(path) as image:cropped=image.crop((x0,y0,x1,y1))
                    cropped.save(path)
                result=self._worker({'kind':kind,'image_path':str(path)})
                items=result.get('items')
                if not isinstance(items,list) or len(items)>64:raise LabelError('读码结果无效',422)
                for item in items:
                    if (not isinstance(item,dict) or not isinstance(item.get('text'),str)
                            or len(item['text'])>4096 or not isinstance(item.get('format'),str)
                            or len(item['format'])>64):raise LabelError('读码结果无效',422)
                    points=item.pop('quad',None)
                    score=item.get('confidence')
                    if (not isinstance(points,list) or len(points)!=4
                            or any(not isinstance(p,list) or len(p)!=2 or
                                   any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in points)
                            or (score is not None and (type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1))):
                        raise LabelError('读码结果无效',422)
                    item['quad_norm']=[[min(1.,max(0.,(float(x)+x0)/width)),
                                        min(1.,max(0.,(float(y)+y0)/height))] for x,y in points]
            return {'kind':kind,'items':items,'width':width,'height':height,
                    'elapsed_ms':round((time.monotonic()-started)*1000,2),'validation':'unverified'}
        finally:self._slot.release()


def router(service=None):
    api=APIRouter();reader=service or ReadingService()
    @api.get('/api/readers')
    def catalog():return reader.catalog()
    @api.post('/api/readers/read')
    def read(file:UploadFile=File(...),kind:str=Form('barcode'),roi:str=Form('')):
        try:region=json.loads(roi) if roi else None
        except ValueError:raise LabelError('ROI不是有效JSON') from None
        return reader.read(file.file,kind,region)
    return api
