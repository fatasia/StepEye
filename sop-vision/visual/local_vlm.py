"""Own one on-demand llama-server process, never an externally started service."""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import json
import math
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from typing import Mapping

import httpx


class LocalVLMError(RuntimeError):
    """Not URLError: the caller must not silently retry startup, busy or timed-out work."""


class LocalVLMBusy(LocalVLMError):
    pass


@dataclass(frozen=True)
class LocalVLMConfig:
    binary: Path
    model: Path
    mmproj: Path
    port: int = 18081
    context_size: int = 4096
    gpu_layers: int = 99
    threads: int = 4
    idle_s: float = 60
    startup_timeout_s: float = 120
    request_timeout_s: float = 15
    model_alias: str = 'stepeye-local'

    def __post_init__(self):
        base = Path(__file__).resolve().parents[1]
        for name in ('binary', 'model', 'mmproj'):
            value = Path(getattr(self, name)).expanduser()
            object.__setattr__(self, name, (value if value.is_absolute() else base / value).resolve())
        for name, low, high in (('port', 1024, 65535), ('context_size', 512, 32768),
                                ('gpu_layers', 0, 999), ('threads', 1, 64)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'local VLM {name} 超出范围')
        for name, maximum in (('idle_s', 3600), ('startup_timeout_s', 600), ('request_timeout_s', 300)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .01 <= value <= maximum:
                raise ValueError(f'local VLM {name} 超出范围')
        if self.model_alias != 'stepeye-local':
            raise ValueError('本地模型别名固定为stepeye-local')

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> LocalVLMConfig | None:
        binary = values.get('STEPEYE_LOCAL_VLM_BIN', '').strip()
        if not binary:
            return None
        model = values.get('STEPEYE_LOCAL_VLM_MODEL', '').strip()
        mmproj = values.get('STEPEYE_LOCAL_VLM_MMPROJ', '').strip()
        if not model or not mmproj:
            raise ValueError('启用本地VLM必须配置MODEL和MMPROJ')
        return cls(Path(binary), Path(model), Path(mmproj),
                   port=int(values.get('STEPEYE_LOCAL_VLM_PORT', '18081')),
                   idle_s=float(values.get('STEPEYE_LOCAL_VLM_IDLE_S', '60')))

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}/v1'

    @property
    def endpoint(self) -> str:
        return self.base_url + '/chat/completions'


class LocalVLMTransport:
    def __init__(self, config: LocalVLMConfig, *, popen=subprocess.Popen, client_factory=httpx.Client):
        self.config = config
        self._popen, self._client_factory = popen, client_factory
        self._cv = threading.Condition()
        self._gate = threading.Lock()
        self._process = None
        self._idle_thread = None
        self._last_used = 0.
        self._closed = self._busy = False
        self._token = ''
        self._starts = 0
        atexit.register(self.close)

    def snapshot(self) -> dict:
        with self._cv:
            alive = self._process is not None and self._process.poll() is None
            return {'state': 'closed' if self._closed else 'busy' if self._busy else 'warm' if alive else 'cold',
                    'running': alive, 'busy': self._busy, 'starts': self._starts,
                    'idle_s': self.config.idle_s, 'gpu_layers_requested': self.config.gpu_layers}

    def _check_port(self):
        # Refuse occupied ports; never attach to or stop an unrelated local server.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(('127.0.0.1', self.config.port))
            except OSError:
                raise LocalVLMError('本地VLM端口已被占用，请由管理员调整端口') from None

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        except (OSError, ProcessLookupError):
            pass

    def _stop_process(self):
        with self._cv:
            process, self._process = self._process, None
            self._token = ''
            self._cv.notify_all()
        self._terminate(process)

    def _start(self, client):
        cfg = self.config
        with self._cv:
            if self._closed:
                raise LocalVLMError('本地VLM服务已关闭')
            if self._process is not None and self._process.poll() is None:
                return
            if not all(path.is_file() for path in (cfg.binary, cfg.model, cfg.mmproj)):
                raise LocalVLMError('本地VLM二进制、GGUF模型或投影文件缺失')
            if cfg.model.suffix.lower() != '.gguf' or cfg.mmproj.suffix.lower() != '.gguf':
                raise LocalVLMError('本地VLM模型和投影必须是GGUF文件')
            self._check_port()
            self._token = secrets.token_urlsafe(32)
            command = [str(cfg.binary), '--model', str(cfg.model), '--mmproj', str(cfg.mmproj),
                       '--host', '127.0.0.1', '--port', str(cfg.port), '--alias', cfg.model_alias,
                       '--ctx-size', str(cfg.context_size), '--n-gpu-layers', str(cfg.gpu_layers),
                       '--threads', str(cfg.threads), '--parallel', '1', '--api-key', self._token]
            try:
                self._process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, shell=False, cwd=str(cfg.binary.parent),
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                raise LocalVLMError('本地VLM进程启动失败，请检查二进制与运行库') from None
            process = self._process
            self._starts += 1
        deadline = time.monotonic() + cfg.startup_timeout_s
        while time.monotonic() < deadline:
            with self._cv:
                if self._closed or self._process is not process:
                    raise LocalVLMError('本地VLM启动已取消')
                if process.poll() is not None:
                    raise LocalVLMError('本地VLM进程已退出，未就绪')
            try:
                response = client.get(f'http://127.0.0.1:{cfg.port}/health',
                                      timeout=min(1., max(.01, deadline - time.monotonic())))
                if response.status_code == 200 and response.json().get('status') == 'ok':
                    return
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            with self._cv:
                self._cv.wait(timeout=min(.1, max(0., deadline - time.monotonic())))
        raise LocalVLMError('本地VLM启动超时，进程将释放')

    def post(self, url: str, headers: dict, payload: dict) -> dict:
        if url != self.config.endpoint:
            raise LocalVLMError('本地VLM仅允许固定回环聊天端点')
        if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False).encode()) > 25 * 1024 * 1024:
            raise LocalVLMError('本地VLM请求无效或超过25MB')
        if not self._gate.acquire(blocking=False):
            raise LocalVLMBusy('本地VLM忙，请稍后显式重试')
        try:
            with self._cv:
                if self._closed:
                    raise LocalVLMError('本地VLM服务已关闭')
                self._busy = True
                self._cv.notify_all()
            with self._client_factory(timeout=self.config.request_timeout_s, trust_env=False,
                                      follow_redirects=False) as client:
                self._start(client)
                with self._cv:
                    if self._closed:
                        raise LocalVLMError('本地VLM服务已关闭')
                    token = self._token
                # Never forward cloud credentials. A token is generated for this owned process only.
                response = client.post(self.config.endpoint, headers={'Authorization': f'Bearer {token}'},
                                       json={**payload, 'model': self.config.model_alias, 'stream': False})
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise LocalVLMError('本地VLM返回了无效响应')
                with self._cv:
                    if self._closed:
                        raise LocalVLMError('本地VLM请求已取消')
                return result
        except LocalVLMError:
            self._stop_process()
            raise
        except Exception:
            self._stop_process()
            raise LocalVLMError('本地VLM请求失败或超时，进程已释放') from None
        finally:
            with self._cv:
                self._busy = False
                self._last_used = time.monotonic()
                if not self._closed and self._process is not None and self._idle_thread is None:
                    self._idle_thread = threading.Thread(target=self._idle, name='local-vlm-idle', daemon=True)
                    self._idle_thread.start()
                self._cv.notify_all()
            self._gate.release()

    def _idle(self):
        while True:
            with self._cv:
                if self._closed or self._process is None:
                    self._idle_thread = None
                    return
                if self._busy:
                    self._cv.wait()
                    continue
                remaining = self.config.idle_s - (time.monotonic() - self._last_used)
                if remaining > 0:
                    self._cv.wait(timeout=remaining)
                    continue
                # Reserve the same gate used by post so idle teardown cannot race a new startup.
                if not self._gate.acquire(blocking=False):
                    self._cv.wait()
                    continue
                process, self._process = self._process, None
                self._token = ''
            try:
                self._terminate(process)
            finally:
                with self._cv:
                    self._idle_thread = None
                    self._cv.notify_all()
                self._gate.release()
            return

    def close(self):
        with self._cv:
            if self._closed:
                return
            self._closed = True
            thread = self._idle_thread
            self._cv.notify_all()
        self._stop_process()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        atexit.unregister(self.close)


_singleton = None
_singleton_guard = threading.Lock()


def get_transport(config: LocalVLMConfig) -> LocalVLMTransport:
    global _singleton
    with _singleton_guard:
        if _singleton is None:
            _singleton = LocalVLMTransport(config)
        elif _singleton.config != config:
            raise LocalVLMError('本机VLM已绑定另一配置，需停止服务后更改')
        return _singleton


def close_all():
    global _singleton
    with _singleton_guard:
        instance, _singleton = _singleton, None
    if instance is not None:
        instance.close()
