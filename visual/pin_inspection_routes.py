"""Bounded one-shot reference/inspection image API; no resident worker."""
import io
import json
import threading

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from .pin_inspection import PinSpec, inspect_pins


def _read(upload):
    raw = upload.file.read(10 * 1024 * 1024 + 1)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, '图片为空或超过 10 MB')
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP', 'BMP') or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('仅支持单帧 PNG/JPEG/WEBP/BMP')
        if image.width * image.height > 4_000_000 or max(image.size) > 4096:
            raise ValueError('图片最多 400 万像素、边长 4096')
        return np.array(ImageOps.exif_transpose(image).convert('RGB'))


def router():
    api = APIRouter()
    slot = threading.Lock()

    @api.post('/api/quality/pins/inspect')
    def inspect(request: Request, reference: UploadFile = File(...), file: UploadFile = File(...),
                spec: str = Form(...)):
        user = getattr(request.state, 'user', None)
        role = user.get('role') if isinstance(user, dict) else getattr(user, 'role', None)
        if role not in ('operator', 'admin'):
            raise HTTPException(403, '需要操作员或管理员权限')
        if len(spec) > 4096:
            raise HTTPException(413, '参数过大')
        if not slot.acquire(False):
            raise HTTPException(429, '已有针脚检查任务，请稍后再试')
        try:
            body = PinSpec.model_validate(json.loads(spec))
            return inspect_pins(_read(reference), _read(file), body)
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, str(exc)[:200]) from None
        finally:
            slot.release()

    return api
