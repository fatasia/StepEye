"""针脚分割试跑：复用模型目录、图片清洗和共享试跑槽，不注册为框检测器。"""
from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from PIL import Image

from .label_jobs import LabelEngines, fingerprint
from .labels import LabelError, MAX_IMAGE_BYTES
from .model_routes import ModelTrials

MAX_PIN_PIXELS = 4_000_000
MAX_MASK_BYTES = 2 * 1024 * 1024


class PinTrials:
    def __init__(self, root, timeout_s=30, slot=None):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError('invalid timeout')
        self.timeout_s = timeout_s
        self._slot = slot if slot is not None else threading.Lock()
        self._guard = threading.Lock()
        self._closed, self._process = False, None
        self.engines = LabelEngines()
        root = Path(root).resolve()
        manifest = root / 'models/public/pin-models.json'
        if not manifest.is_file():
            return
        data = json.loads(manifest.read_text(encoding='utf8'))
        if data['schema_version'] != 1:
            raise ValueError('unsupported pin manifest')
        for item in data['models']:
            path = (root / item['model_path']).resolve()
            if (item['task'] != 'pin_segmentation' or item['classes'] != ['background', 'pin']
                    or not path.is_relative_to(root / 'models/public') or path.suffix != '.onnx'):
                raise ValueError('unsupported pin model entry')
            self.engines.register(item['id'], path, item['classes'], item['name'], metadata={
                'expected_sha256': item['sha256'], 'license_status': item['license_status'],
                'source': item['source'], 'usage': 'evaluation_only'})

    def catalog(self):
        return [{**row, 'task': 'segment', 'runtime': 'onnx', 'validation': 'unverified',
                 'available': row['available'] and not self._closed,
                 'reason': '试跑服务已关闭' if self._closed else row['reason']}
                for row in self.engines.list()]

    def trial(self, engine_id, upload):
        if not self._slot.acquire(False):
            raise LabelError('已有模型试测正在运行，请稍后重试', 429)
        started = time.monotonic()
        try:
            with self._guard:
                if self._closed:
                    raise LabelError('针脚试跑服务已关闭', 503)
            model = self.engines.snapshot(engine_id)
            raw = upload.read(MAX_IMAGE_BYTES + 1)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                raise LabelError('图片不能为空且最多20MB', 413)
            with tempfile.TemporaryDirectory(prefix='stepeye-pin-trial-') as folder:
                image = Path(folder) / 'image.png'
                width, height = ModelTrials._image(raw, image)
                # Native-scale tiling is intentionally bounded; no silent downsampling.
                patches = ((width + 511) // 512) * ((height + 511) // 512)
                if width * height > MAX_PIN_PIXELS or patches > 16:
                    raise LabelError('针脚试跑最多400万像素、16个切块，请裁剪检测区域', 413)
                mask = Path(folder) / 'mask.png'
                stats = self._run({**model, 'image_path': str(image), 'mask_path': str(mask)})
                with mask.open('rb') as stream:
                    encoded = stream.read(MAX_MASK_BYTES + 1)
                if len(encoded) > MAX_MASK_BYTES:
                    raise LabelError('针脚掩膜超过输出限制', 422)
                try:
                    with Image.open(io.BytesIO(encoded)) as result:
                        if result.format != 'PNG' or result.mode != 'L' or result.size != (width, height):
                            raise ValueError('mask dimensions')
                        colors = result.getcolors(3)
                        if colors is None or any(value not in (0, 255) for _, value in colors):
                            raise ValueError('mask classes')
                        foreground = sum(count for count, value in colors if value == 255)
                    patches = ((width + 511) // 512) * ((height + 511) // 512)
                    if (stats['foreground_pixels'] != foreground or stats['patch_count'] != patches
                            or type(stats['inference_ms']) not in (int, float)
                            or not math.isfinite(stats['inference_ms']) or stats['inference_ms'] < 0):
                        raise ValueError('mask statistics')
                except (ValueError, KeyError, TypeError, OSError):
                    raise LabelError('针脚试跑返回无效掩膜', 422) from None
            if fingerprint(Path(model['model_path'])) != model['model_sha256']:
                raise LabelError('试跑期间模型发生变化', 409)
            return {'task': 'segment', 'model_id': engine_id, 'model_sha256': model['model_sha256'],
                    'classes': model['classes'], 'width': width, 'height': height,
                    'mask_png_base64': base64.b64encode(encoded).decode('ascii'),
                    'mask_encoding': 'binary_png_0_255', 'foreground_pixels': foreground,
                    'patch_count': patches, 'inference_ms': stats['inference_ms'],
                    'elapsed_ms': round((time.monotonic() - started) * 1000, 2),
                    'provider': 'CPUExecutionProvider', 'validation': 'unverified',
                    'usage': 'evaluation_only', 'defect_verdict': None}
        except OSError:
            raise LabelError('无法完成针脚试跑，请检查模型与运行库', 503) from None
        finally:
            self._slot.release()

    def _run(self, spec):
        with tempfile.TemporaryFile() as output:
            with self._guard:
                if self._closed:
                    raise LabelError('针脚试跑服务已关闭', 503)
                process = subprocess.Popen([sys.executable, '-m', 'visual.pin_predict'],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                    stdout=output, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self._process = process
            try:
                process.communicate(json.dumps(spec).encode(), timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise LabelError('针脚试跑超时，进程已停止', 504) from None
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()
                with self._guard:
                    self._process = None
            output.seek(0)
            raw = output.read(8193)
        with self._guard:
            if self._closed:
                raise LabelError('针脚试跑服务已关闭', 503)
        if process.returncode or len(raw) > 8192:
            raise LabelError('针脚模型推理失败', 422)
        try:
            return json.loads(raw)
        except ValueError:
            raise LabelError('针脚模型返回无效结果', 422) from None

    def close(self):
        with self._guard:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
