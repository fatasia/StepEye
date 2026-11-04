"""固定表盘Pose模型的单图评估；量程、单位和旋向由调用方明确提供。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MODEL_ID = 'public-gauge-pose-e120'
MODEL_SHA256 = 'c5fd23e05397d75730dbef2d561ccb99fcdec8bc89a3148104559e633f139441'
MODEL_BYTES = 12598927
KEYPOINTS = ('min', 'max', 'tip', 'center')
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e12, le=1e12)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                      max_length=32, pattern=r'^[^\x00-\x1f<>]+$')]


class GaugeError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class GaugeCalibration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    min_value: Number
    max_value: Number
    unit: Unit
    direction: Literal['clockwise', 'counterclockwise']

    @model_validator(mode='after')
    def ordered(self):
        if self.max_value <= self.min_value:
            raise ValueError('量程上限必须大于下限')
        return self


def geometry(points, width, height, calibration=None):
    """归一坐标先还原像素，避免宽高不同导致角度畸变；不猜旋向或限幅。"""
    result = {'status': 'unknown', 'reading': None, 'ratio': None, 'unit': None,
              'angles_deg': {}, 'reason': ''}
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or not isinstance(points, (list, tuple)) or len(points) != 4):
        result['reason'] = 'invalid_keypoints'
        return result
    try:
        if any(len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v)
                                 or not 0 <= v <= 1 for v in p) for p in points):
            raise ValueError
        pixels = [(p[0] * width, p[1] * height) for p in points]
    except (TypeError, ValueError):
        result['reason'] = 'invalid_keypoints'
        return result
    center = pixels[3]
    vectors = [(p[0] - center[0], p[1] - center[1]) for p in pixels[:3]]
    if any(math.hypot(x, y) < 1 for x, y in vectors):
        result['reason'] = 'degenerate_keypoint_radius'
        return result
    angles = [math.degrees(math.atan2(y, x)) % 360 for x, y in vectors]
    result['angles_deg'] = dict(zip(KEYPOINTS[:3], angles))
    if calibration is None:
        result.update(status='uncalibrated', reason='explicit_range_unit_direction_required')
        return result
    calibration = (calibration if isinstance(calibration, GaugeCalibration)
                   else GaugeCalibration.model_validate(calibration))
    sign = 1 if calibration.direction == 'clockwise' else -1
    span = (sign * (angles[1] - angles[0])) % 360
    travel = (sign * (angles[2] - angles[0])) % 360
    result.update(unit=calibration.unit, sweep_degrees=span, needle_travel_degrees=travel)
    if span < 5 or span > 355:
        result['reason'] = 'degenerate_scale_span'
    elif travel > span + 1e-6:
        result['reason'] = 'needle_outside_configured_sweep'
    else:
        ratio = travel / span
        result.update(status='reading', reason='', ratio=ratio,
                      reading=calibration.min_value + ratio * (calibration.max_value - calibration.min_value))
    return result


def pinned_model(root):
    directory = Path(root).resolve() / 'models/public'
    path = directory / 'gauge_pose_e120.onnx'
    try:
        if (not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size != MODEL_BYTES
                or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256):
            raise GaugeError('表盘模型指纹不匹配', 503)
    except OSError:
        raise GaugeError('固定表盘模型尚未安装', 503) from None
    return path


class GaugeService:
    """一个按需CPU子进程；没有模型缓存、常驻线程或普通检测器回退。"""
    def __init__(self, root, timeout_s=10):
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or not .01 <= timeout_s <= 60:
            raise ValueError('表盘试测超时须在0.01–60秒之间')
        self.root, self.timeout_s = Path(root).resolve(), timeout_s
        self._slot, self._lock = threading.Lock(), threading.Lock()
        self._closed, self._process = False, None

    def capabilities(self):
        try:
            pinned_model(self.root)
            available = all(importlib.util.find_spec(name) is not None for name in ('onnxruntime', 'cv2'))
            reason = '' if available else 'optional_runtime_missing'
        except GaugeError:
            available, reason = False, 'fixed_model_unavailable'
        with self._lock:
            closed = self._closed
        return {'model_id': MODEL_ID, 'task': 'gauge_pose', 'available': available and not closed,
                'reason': 'closed' if closed else reason, 'model_sha256': MODEL_SHA256,
                'keypoints': list(KEYPOINTS), 'calibration_required_for_reading': True,
                'directions': ['clockwise', 'counterclockwise'], 'usage': 'evaluation_only',
                'license_status': 'AGPL-3.0 metadata; source/image license review required'}

    def trial(self, raw: bytes, calibration=None):
        from .labels import LabelError, MAX_IMAGE_BYTES
        from .model_routes import ModelTrials
        if calibration is not None:
            calibration = (calibration if isinstance(calibration, GaugeCalibration)
                           else GaugeCalibration.model_validate(calibration))
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_IMAGE_BYTES:
            raise GaugeError('表盘试图不能为空且最多20MB', 413)
        if not self._slot.acquire(False):
            raise GaugeError('表盘试测忙，请稍后重试', 429)
        started = time.monotonic()
        try:
            with self._lock:
                if self._closed:
                    raise GaugeError('表盘试测服务已关闭', 503)
            pinned_model(self.root)
            with tempfile.TemporaryDirectory(prefix='stepeye-gauge-') as folder:
                image = Path(folder) / 'image.png'
                try:
                    width, height = ModelTrials._image(raw, image)
                except LabelError as exc:
                    raise GaugeError(str(exc), exc.status) from None
                detections, inference_ms = self._run(folder, image)
            for detection in detections:
                try:
                    box = detection['bbox_xyxy_norm']
                    if (set(detection) != {'bbox_xyxy_norm', 'confidence', 'keypoints'}
                            or len(box) != 4 or len(detection['keypoints']) != 4
                            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                                   for v in [*box, detection['confidence']])
                            or box[0] >= box[2] or box[1] >= box[3]):
                        raise ValueError
                    for point, name in zip(detection['keypoints'], KEYPOINTS):
                        if (set(point) != {'name', 'x_norm', 'y_norm', 'confidence'} or point['name'] != name
                                or any(type(point[k]) not in (int, float) or not math.isfinite(point[k])
                                       or not 0 <= point[k] <= 1 for k in ('x_norm', 'y_norm', 'confidence'))):
                            raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise GaugeError('表盘推理返回无效关键点', 503) from None
            calculated = {'status': 'no_detection' if not detections else 'multiple_gauges',
                          'reading': None, 'ratio': None, 'unit': None, 'angles_deg': {}}
            if len(detections) == 1:
                points = [[p['x_norm'], p['y_norm']] for p in detections[0]['keypoints']]
                calculated = geometry(points, width, height, calibration)
            return {'task': 'gauge_pose', 'model_id': MODEL_ID, 'model_sha256': MODEL_SHA256,
                    'width': width, 'height': height, 'detections': detections, **calculated,
                    'calibration': None if calibration is None else calibration.model_dump(),
                    'inference_ms': inference_ms, 'elapsed_ms': round((time.monotonic() - started) * 1000, 3),
                    'provider': 'CPUExecutionProvider', 'usage': 'evaluation_only',
                    'production_approval': False, 'supports_nonlinear_scale': False,
                    'review_required': True, 'alarm_generated': False}
        finally:
            self._slot.release()

    def _run(self, folder, image):
        request = Path(folder) / 'request.json'
        request.write_text(json.dumps({'root': str(self.root), 'image_path': str(image)}), encoding='utf8')
        with tempfile.TemporaryFile() as output:
            with self._lock:
                if self._closed:
                    raise GaugeError('表盘试测服务已关闭', 503)
                try:
                    process = subprocess.Popen([sys.executable, '-m', 'visual.gauge_worker',
                        '--request', str(request)], cwd=Path(__file__).resolve().parents[1],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except OSError:
                    raise GaugeError('无法启动表盘隔离进程', 503) from None
                self._process = process
            try:
                process.communicate(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise GaugeError('表盘试测超时，进程已终止', 504) from None
            finally:
                if process.poll() is None:
                    try: process.kill()
                    except OSError: pass
                process.communicate()
                with self._lock:
                    if self._process is process: self._process = None
            output.seek(0); raw = output.read(32769)
        if process.returncode or len(raw) > 32768:
            raise GaugeError('表盘隔离推理失败', 503)
        pinned_model(self.root)
        try:
            row = json.loads(raw)
            if (row['model_sha256'] != MODEL_SHA256 or not isinstance(row['detections'], list)
                    or len(row['detections']) > 8 or type(row['inference_ms']) not in (int, float)
                    or not math.isfinite(row['inference_ms']) or row['inference_ms'] < 0):
                raise ValueError
            return row['detections'], row['inference_ms']
        except (ValueError, KeyError, TypeError):
            raise GaugeError('表盘隔离推理返回无效结果', 503) from None

    def close(self):
        with self._lock:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                try: self._process.kill()
                except OSError: pass
