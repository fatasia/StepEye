"""Registered-model catalog and disposable CPU trials; never writes annotation truth."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import warnings

from fastapi import APIRouter, File, Form, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .label_jobs import LabelEngines, fingerprint
from .labels import LabelError, LabelStore, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, _targets


class ModelProjectBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=100)


class ModelTrials:
    def __init__(self, engines: LabelEngines, timeout_s: float = 30):
        self.engines, self.timeout_s = engines, timeout_s
        self._slot = threading.Lock()

    def catalog(self) -> list[dict]:
        return [{**m, 'task': 'detect', 'runtime': 'onnx', 'validation': 'unverified'}
                for m in self.engines.list()]

    def registered(self, engine_id: str) -> dict:
        found = next((m for m in self.catalog() if m['id'] == engine_id), None)
        if found is None:
            raise LabelError('模型未登记', 404)
        return found

    @staticmethod
    def _image(raw: bytes, target: Path) -> tuple[int, int]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('JPEG', 'PNG', 'BMP', 'WEBP') or getattr(image, 'n_frames', 1) != 1:
                        raise LabelError('仅支持单帧 JPEG/PNG/WebP/BMP 图片')
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise LabelError('图片像素超过4000万', 413)
                    image.load()
                    upright = ImageOps.exif_transpose(image)
                    if 'A' in upright.getbands() or 'transparency' in upright.info:
                        rgba = upright.convert('RGBA')
                        upright = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba)
                    clean = Image.new('RGB', upright.size)
                    clean.paste(upright.convert('RGB'))
                    clean.save(target, 'PNG')
                    return clean.size
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise LabelError('图片像素超过安全限制', 413) from None
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            raise LabelError('图片无法完整解码') from None

    def trial(self, engine_id: str, upload, threshold: float = .4) -> dict:
        # Include upload reading and image decoding in the single active trial slot.
        if not self._slot.acquire(blocking=False):
            raise LabelError('已有模型试测正在运行，请稍后重试', 429)
        started = time.monotonic()
        try:
            if not math.isfinite(threshold) or not .05 <= threshold <= .95:
                raise LabelError('阈值须为0.05–0.95')
            entry = self.registered(engine_id)
            if not entry['available']:
                raise LabelError(entry['reason'], 409)
            raw = upload.read(MAX_IMAGE_BYTES + 1)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                raise LabelError('图片不能为空且最多20MB', 413)
            model = self.engines.snapshot(engine_id)
            with tempfile.TemporaryDirectory(prefix='stepeye-model-trial-') as folder:
                image_path = Path(folder) / 'image.png'
                width, height = self._image(raw, image_path)
                spec = {**model, 'image_path': str(image_path), 'project_classes': model['classes'],
                        'threshold': threshold}
                # File stdout bounds parent memory even when a worker fails noisily.
                with tempfile.TemporaryFile() as output:
                    process = subprocess.Popen([sys.executable, '-m', 'visual.label_predict'],
                        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                        stdout=output, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    try:
                        process.communicate(json.dumps(spec).encode(), timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        raise LabelError('模型试测超过30秒，进程已停止', 504) from None
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate()
                    output.seek(0)
                    out = output.read(256 * 1024 + 1)
                if process.returncode or len(out) > 256 * 1024:
                    raise LabelError('试测失败，请检查模型格式与运行库', 422)
                if fingerprint(Path(model['model_path'])) != model['model_sha256']:
                    raise LabelError('试测期间模型发生变化，请重试', 409)
                try:
                    values = json.loads(out)['targets']
                    if not isinstance(values, list) or len(values) > 500:
                        raise ValueError
                    targets = _targets(values, model['classes'])
                except (ValueError, KeyError, TypeError, LabelError):
                    raise LabelError('模型试测返回了无效结果', 422) from None
            return {'model_id': engine_id, 'model_sha256': model['model_sha256'], 'classes': model['classes'],
                    'targets': targets, 'width': width, 'height': height,
                    'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
                    'provider': 'CPUExecutionProvider', 'validation': 'unverified'}
        except OSError:
            raise LabelError('无法启动模型试测，请检查本机运行环境', 503) from None
        finally:
            self._slot.release()


def router(labels: LabelStore, engines: LabelEngines) -> APIRouter:
    api = APIRouter()
    trials = ModelTrials(engines)

    @api.get('/api/models')
    def catalog() -> list[dict]:
        return trials.catalog()

    @api.post('/api/models/{engine_id}/trial')
    def trial(engine_id: str, file: UploadFile = File(...), threshold: float = Form(.4)) -> dict:
        return trials.trial(engine_id, file.file, threshold)

    @api.post('/api/models/{engine_id}/project', status_code=201)
    def project(engine_id: str, body: ModelProjectBody) -> dict:
        model = trials.registered(engine_id)
        return labels.create_project(body.name.strip() or model['name'][:100], model['classes'])

    return api
