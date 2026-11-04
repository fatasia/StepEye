"""FEMTO similarity RUL research inference, trend-level only, isolated per request."""
from __future__ import annotations
import hashlib, json, subprocess, sys, threading, tempfile
from pathlib import Path
from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator
import numpy as np

REFERENCE_SHA = 'd82ece89984b9c736029b2579a79c4c04ac10f27b03c004f39d613ec9f5c3dfc'
MANIFEST_SHA = '0560666cb24cd4ca5d0a7b99444e088eca5d1fe2409807f83fa754ee9b08fbfb'


class DegError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class DegWindow(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    unit_id: str = Field(min_length=1, max_length=100)
    # 观测起点基线 RMS：观测段前 3 分钟逐点 max(rms_h, rms_v) 的中位数。
    baseline_rms: Finite = Field(gt=0)
    # 每分钟一点的双通道逐点最大 RMS 序列（与训练参考同一采样协议）。
    rms_values: list[Finite] = Field(min_length=5, max_length=600)
    @model_validator(mode='after')
    def positive(self):
        if any(v <= 0 for v in self.rms_values):
            raise ValueError('rms_values must be positive')
        return self


class DegBody(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field(default='', max_length=200)
    dataset: Literal['FEMTO-PRONOSTIA-LearningSet']
    unit: Literal['minute'] = 'minute'
    windows: list[DegWindow] = Field(min_length=1, max_length=8)
    @model_validator(mode='after')
    def contract(self):
        ids = [w.unit_id for w in self.windows]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate unit window')
        return self


def pinned_bundle(root):
    bundle = Path(root) / 'models/mechanism/femto-degradation'
    for name, digest in [('reference.json', REFERENCE_SHA), ('manifest.json', MANIFEST_SHA)]:
        path = bundle / name
        if not path.is_file() or path.is_symlink():
            raise DegError('退化 RUL 资产不可用', 503)
        with path.open('rb') as f:
            actual = hashlib.file_digest(f, 'sha256').hexdigest()
        if actual != digest:
            raise DegError('退化 RUL 资产指纹不匹配', 409)
    return bundle


class DegradationService:
    def __init__(self, base_dir):
        self.root = Path(base_dir).resolve()
        self.lock = threading.Lock()
        self.closed = False

    def capabilities(self):
        try:
            pinned_bundle(self.root)
            available, reason = True, ''
        except DegError as e:
            available, reason = False, str(e)
        return {'available': available and not self.closed, 'reason': reason,
                'dataset': 'FEMTO-PRONOSTIA-LearningSet', 'unit': 'minute',
                'window_points': [5, 600], 'max_windows': 8,
                'reference_sha256': REFERENCE_SHA,
                'scope': 'trend-level similarity RUL on bearing vibration RMS; not a precision RUL model'}

    def analyze(self, body):
        body = body if isinstance(body, DegBody) else DegBody.model_validate(body)
        if self.closed:
            raise DegError('退化 RUL 服务已关闭', 503)
        if not self.lock.acquire(False):
            raise DegError('退化 RUL 服务忙', 429)
        try:
            pinned_bundle(self.root)
            payload = {'root': str(self.root), 'body': body.model_dump()}
            try:
                with tempfile.TemporaryDirectory(prefix='stepeye-degradation-') as temporary:
                    request = Path(temporary) / 'request.json'
                    encoded = json.dumps(payload, allow_nan=False).encode('utf-8')
                    if len(encoded) > 512 * 1024:
                        raise DegError('退化 RUL 请求超过 IPC 上限', 413)
                    request.write_bytes(encoded)
                    p = subprocess.run(
                        [sys.executable, '-m', 'visual.mechanism_degradation_worker', '--request', str(request)],
                        stdin=subprocess.DEVNULL, text=True, capture_output=True,
                        cwd=str(Path(__file__).resolve().parents[1]), timeout=30)
            except subprocess.TimeoutExpired:
                raise DegError('退化 RUL 推理超时', 504) from None
            except OSError:
                raise DegError('退化 RUL worker 不可用', 503) from None
            if p.returncode:
                raise DegError('退化 RUL worker 拒绝输入或资产', 422)
            return json.loads(p.stdout)
        finally:
            self.lock.release()

    def close(self):
        self.closed = True
        return not self.lock.locked()
