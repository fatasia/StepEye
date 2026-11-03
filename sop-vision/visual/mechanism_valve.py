"""冻结阀门双证据图的隔离影子分析；不控制设备、不生成报警。"""
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

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MODEL_SHA256 = 'c5250ae6d94a8f2a4b755a538451e24496200c2864e4a01aa56ce3d84c45fa67'
MODEL_BYTES = 435073
MODEL_ID = 'weihu-valve-fusion-20261031'
SOURCE_REVISION = '01d77002e960b93254f137a8dd7e618abb12c3ae'
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
TimeValue = Annotated[Number, Field(ge=-1e10, le=1e10)]
Pressure = Annotated[Number, Field(ge=-1e6, le=1e6)]
Identity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TimeSeries = Annotated[list[TimeValue], Field(min_length=400, max_length=400)]


class ValveError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class CommandEdge(BaseModel):
    model_config = ConfigDict(extra='forbid')
    time_s: TimeValue
    clock_id: Identity
    source: Identity
    kind: Literal['controller_command', 'digital_input', 'replay_fixture']


class PressureTimes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    PS1: TimeSeries | None = None
    PS2: TimeSeries | None = None


class ValveBody(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field(default='', max_length=200)
    channels: tuple[Literal['PS1'], Literal['PS2']]
    units: tuple[Literal['bar'], Literal['bar']]
    sample_rate_hz: Literal[100]
    values: list[tuple[Pressure, Pressure]] = Field(min_length=400, max_length=400)
    clock_id: Identity | None = None
    timestamps_s: PressureTimes | None = None
    command_edge: CommandEdge | None = None

    @property
    def unit(self):
        return 'bar'


def validate_timestamps(body: ValveBody) -> dict:
    """校验上传时标声明；这些值不是可信设备来源证明。"""
    reasons, times, offsets = [], {}, {}
    if body.clock_id is None:
        reasons.append({'code': 'missing_pressure_clock'})
    edge = body.command_edge
    if edge is None:
        reasons.append({'code': 'no_independent_command_edge'})
    elif body.clock_id != edge.clock_id:
        reasons.append({'code': 'clock_id_mismatch'})
    for channel in ('PS1', 'PS2'):
        submitted = getattr(body.timestamps_s, channel, None)
        if submitted is None:
            reasons.append({'code': 'missing_channel_timestamps', 'channel': channel})
            continue
        t = np.asarray(submitted, dtype=np.float64)
        times[channel] = t
        # 100Hz节拍、无缺样或逆序，且累计采样相位偏差不超过1点。
        if np.any(np.abs(np.diff(t) - .01) > .0001 + 1e-6):
            reasons.append({'code': 'timestamp_gap_or_order', 'channel': channel})
        if np.max(np.abs(t - t[0] - np.arange(400) / 100)) > .01 + 1e-6:
            reasons.append({'code': 'timestamp_grid_drift', 'channel': channel})
        if edge is not None:
            offset = float((t[0] - edge.time_s - 8) * 100)
            offsets[channel] = offset
            if abs(offset) > 1 + 1e-4:
                reasons.append({'code': 'command_window_offset', 'channel': channel,
                                'offset_samples': offset})
    skew = None
    if len(times) == 2:
        skew = float(np.max(np.abs(times['PS1'] - times['PS2'])) * 100)
        if skew > 1 + 1e-4:
            reasons.append({'code': 'channel_skew', 'max_skew_samples': skew})
    return {'status': 'unsupported' if reasons else 'supported', 'reasons': reasons,
            'offset_samples': offsets, 'max_channel_skew_samples': skew,
            'tolerance_samples': 1, 'expected_lead_s': 8,
            'verification': 'submitted_timestamp_consistency_not_device_attestation'}


def pinned_asset(root: Path) -> Path:
    """只接受本地固定目录、固定清单、固定SHA；HTTP输入不能选择文件。"""
    directory = Path(root).resolve() / 'models/mechanism/valve-fusion'
    model, manifest = directory / 'ae.onnx', directory / 'manifest.json'
    try:
        item = json.loads(manifest.read_text(encoding='utf8'))
        expected = {'file': 'ae.onnx', 'sha256': MODEL_SHA256, 'inputName': 'values',
                    'shape': [None, 400, 2], 'outputIndex': 0, 'transform': 'identity', 'threshold': 1.0}
        if (not model.resolve().is_relative_to(directory.resolve())
                or item['schemaVersion'] != 1 or item['bundleKind'] != 'valve-fusion-runtime'
                or item['channels'] != ['PS1', 'PS2'] or item['units'] != ['bar', 'bar']
                or item['sampleRateHz'] != 100 or item['windowSeconds'] != 4
                or item['windowStartSeconds'] != 8
                or item['sync'] != {'required': True, 'expected_lead_s': 8.0, 'tolerance_samples': 1}
                or item['models'] != {'ae': expected}):
            raise ValveError('阀门模型清单与固定契约不一致', 503)
        if model.stat().st_size != MODEL_BYTES or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValveError('阀门模型校验失败', 503)
    except (OSError, ValueError, KeyError, TypeError):
        raise ValveError('阀门固定模型未安装或清单损坏', 503) from None
    return model


class ValveShadow:
    """单槽、按需一次性子进程；close仅终止本实例的子进程。"""
    def __init__(self, root: Path, timeout_s=10):
        if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or not .01 <= timeout_s <= 60:
            raise ValueError('阀门分析超时须在0.01–60秒之间')
        self.root, self.timeout_s = Path(root).resolve(), timeout_s
        self._slot, self._lock = threading.Lock(), threading.Lock()
        self._closed, self._process = False, None

    def capabilities(self):
        try:
            pinned_asset(self.root)
            available = importlib.util.find_spec('onnxruntime') is not None
            reason = '' if available else 'onnxruntime_missing'
        except ValveError:
            available, reason = False, 'fixed_model_unavailable'
        with self._lock:
            closed = self._closed
        return {'method': 'valve_fusion_shadow', 'model_id': MODEL_ID,
                'model_sha256': MODEL_SHA256, 'available': available and not closed,
                'reason': 'closed' if closed else reason, 'channels': ['PS1', 'PS2'],
                'units': ['bar', 'bar'], 'sample_rate_hz': 100, 'samples': 400,
                'window_start_after_command_s': 8, 'window_seconds': 4,
                'sync_tolerance_samples': 1, 'historical_threshold': 1,
                'usage': 'evaluation_only', 'trusted_device_source': False,
                'production_approval': False, 'supports_rul': False}

    def analyze(self, body: ValveBody) -> dict:
        body = body if isinstance(body, ValveBody) else ValveBody.model_validate(body)
        if not self._slot.acquire(False):
            raise ValveError('阀门影子分析忙，请稍后重试', 429)
        try:
            with self._lock:
                if self._closed:
                    raise ValveError('阀门影子分析已关闭', 503)
            quality = validate_timestamps(body)
            result = {'method': 'valve_fusion_shadow', 'status': 'unsupported', 'quality': quality,
                      'model_id': MODEL_ID, 'model_sha256': MODEL_SHA256,
                      'sample_rate_hz': 100, 'samples': 400, 'duration_s': 4, 'unit': 'bar',
                      'score': None, 'historical_threshold': 1, 'threshold_exceeded': None,
                      'score_meaning': 'min(normalized_AE_top10pct_abs_residual,normalized_timing_deviation)',
                      'validation_scope': ('replay_fixture' if body.command_edge and
                          body.command_edge.kind == 'replay_fixture' else 'uploaded_timestamp_claims'),
                      'trusted_device_source': False, 'usage': 'evaluation_only',
                      'production_approval': False, 'review_required': True,
                      'supports_rul': False, 'alarm_generated': False, 'control_effect': 'none',
                      'source_revision': SOURCE_REVISION}
            if quality['status'] != 'supported':
                return result
            pinned_asset(self.root)
            started = time.monotonic()
            worker = self._run(body.values)
            result.update(status='shadow_evidence', score=worker['score'],
                          threshold_exceeded=worker['score'] > 1,
                          inference_ms=worker['inference_ms'], provider='CPUExecutionProvider',
                          elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            return result
        finally:
            self._slot.release()

    def _run(self, values):
        # Windows communicate() may block writing a full stdin pipe before its
        # timeout starts. A private bounded request file keeps all IPC under wait.
        with tempfile.TemporaryDirectory(prefix='stepeye-valve-') as folder, tempfile.TemporaryFile() as output:
            request = Path(folder) / 'request.json'
            raw_request = json.dumps({'root': str(self.root), 'values': values}, allow_nan=False).encode()
            if len(raw_request) > 64 * 1024:
                raise ValveError('阀门推理输入超过内部容量限制')
            request.write_bytes(raw_request)
            with self._lock:
                if self._closed:
                    raise ValveError('阀门影子分析已关闭', 503)
                try:
                    process = subprocess.Popen([sys.executable, '-m', 'visual.mechanism_valve_worker',
                                                '--request', str(request)],
                        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                        stdout=output, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except OSError:
                    raise ValveError('无法启动阀门隔离推理进程', 503) from None
                self._process = process
            try:
                process.communicate(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise ValveError('阀门影子分析超时，进程已终止', 504) from None
            finally:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass  # Exit may race with timeout/close; only our handle is used.
                process.communicate()
                with self._lock:
                    if self._process is process:
                        self._process = None
            output.seek(0)
            raw = output.read(8193)
        if process.returncode or len(raw) > 8192:
            raise ValveError('阀门隔离推理失败，未保存正常判定', 503)
        pinned_asset(self.root)
        try:
            row = json.loads(raw)
            if (row['model_sha256'] != MODEL_SHA256 or type(row['score']) not in (int, float)
                    or not math.isfinite(row['score']) or row['score'] < 0
                    or type(row['inference_ms']) not in (int, float)
                    or not math.isfinite(row['inference_ms']) or row['inference_ms'] < 0):
                raise ValueError
            return row
        except (ValueError, KeyError, TypeError):
            raise ValveError('阀门隔离推理返回无效证据', 503) from None

    def close(self):
        with self._lock:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    pass
