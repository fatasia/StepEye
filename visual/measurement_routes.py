"""自动双边缘测量入口；权限由服务端现有标定路由策略控制。"""
import io
import json
import threading
from fastapi import APIRouter,File,Form,UploadFile,HTTPException
from PIL import Image,ImageOps,UnidentifiedImageError
import numpy as np
from .measurement import measure_edges,EdgeMeasureSpec


def router(store):
    api=APIRouter();slot=threading.Lock()
    @api.post('/api/calibrations/{calibration_id}/measure-edges')
    def measure(calibration_id:str,file:UploadFile=File(...),spec:str=Form(...)):
        if len(spec)>16384:raise HTTPException(413,'测量参数过大')
        if not slot.acquire(False):raise HTTPException(429,'已有自动测量任务')
        try:
            body=EdgeMeasureSpec.model_validate(json.loads(spec))
            raw=file.file.read(10*1024*1024+1)
            if not raw or len(raw)>10*1024*1024:raise HTTPException(413,'图片为空或超过10MB')
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG','JPEG','WEBP','BMP') or getattr(image,'n_frames',1)!=1:
                    raise ValueError('仅支持单帧常用图片')
                if image.width*image.height>4_000_000 or max(image.size)>4096:raise ValueError('图片过大')
                image.load();array=np.array(ImageOps.exif_transpose(image).convert('RGB'))
            return measure_edges(store,calibration_id,array,body)
        except KeyError:raise HTTPException(404,'标定版本不存在') from None
        except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError) as exc:
            raise HTTPException(422,str(exc)[:200]) from None
        finally:slot.release()
    return api
