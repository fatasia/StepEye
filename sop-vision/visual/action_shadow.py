"""有界、按需启动的动作影子旁路。结果只供监控，不进入生产规则引擎。"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .dsl import ActionShadowCfg

FRAMES, SIZE, SAMPLE_HZ, SUBMIT_S = 16, 224, 8, .5


@dataclass(frozen=True)
class Window:
    id: int
    epoch: int
    identity: tuple[int, str | None, str]
    start_ts: float
    end_ts: float
    end_mono: float
    submitted_mono: float
    clip: np.ndarray


def _predictor(model, names, **options):
    # ORT/模型在原生子进程内加载；采集进程不导入 torch 或 ORT。
    from .action_predict import ActionPredictor
    return ActionPredictor(model, names, **options)


class ActionScheduler:
    """一个进程一个串行执行器，跨工位轮询，每工位仅保留最新 pending。"""
    def __init__(self, factory: Callable = _predictor, clock: Callable = time.monotonic, max_stations: int = 32):
        self.factory, self.clock, self.max_stations = factory, clock, max_stations
        self._cv = threading.Condition()
        self._owners: dict[str, ActionShadow] = {}
        self._pending: OrderedDict[str, Window] = OrderedDict()
        self._thread: threading.Thread | None = None

    def register(self, owner: ActionShadow) -> bool:
        with self._cv:
            if len(self._owners) >= self.max_stations:
                return False
            self._owners[owner.key] = owner
            return True

    def submit(self, owner: ActionShadow, window: Window) -> bool:
        with self._cv:
            if owner.key not in self._owners:
                return False
            replaced = owner.key in self._pending
            self._pending[owner.key] = window  # 更新保留原轮次，不能饿死别的工位。
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="action-shadow", daemon=True)
                self._thread.start()
            self._cv.notify()
            return replaced

    def discard(self, owner: ActionShadow, *, unregister: bool = False) -> None:
        with self._cv:
            self._pending.pop(owner.key, None)
            if unregister:
                self._owners.pop(owner.key, None)
            self._cv.notify()

    def _run(self) -> None:
        runner, loaded_key = None, None
        idle_s = 60.
        try:
            while True:
                with self._cv:
                    if not self._pending:
                        # 无轮询；最后一次任务后定时释放模型与进程。
                        notified = self._cv.wait_for(lambda: bool(self._pending) or not self._owners, timeout=idle_s)
                        if not self._pending and (not notified or not self._owners):
                            break
                    if not self._pending:
                        continue
                    key, window = self._pending.popitem(last=False)
                    owner = self._owners.get(key)
                if owner is None:
                    continue
                cfg = owner.cfg
                idle_s = cfg.idle_s
                if self.clock() - window.end_mono > cfg.ttl_ms / 1000:
                    owner._complete(window, None, "expired", 0., 0.)
                    continue
                model_key = (str(owner.model), tuple(cfg.class_names), tuple(cfg.providers), cfg.model_sha256, cfg.timeout_s)
                cold = runner is None or model_key != loaded_key
                owner._started(window, cold)
                start = self.clock()
                wait_ms = max(0., (start - window.submitted_mono) * 1000)
                try:
                    if cold:
                        if runner is not None:
                            runner.close()
                        runner, loaded_key = None, None
                        runner = self.factory(owner.model, cfg.class_names, timeout_s=cfg.timeout_s,
                                              providers=tuple(cfg.providers), expected_sha256=cfg.model_sha256 or None)
                        loaded_key = model_key
                    out = runner.predict(window.clip)
                    if cfg.model_sha256 and out.get("model_sha256", "").lower() != cfg.model_sha256.lower():
                        raise ValueError("model_hash_mismatch")
                    owner._complete(window, out, "", wait_ms, max(0., (self.clock() - start) * 1000))
                except Exception as exc:
                    owner._complete(window, None, type(exc).__name__, wait_ms, max(0., (self.clock() - start) * 1000))
                    if runner is not None:
                        runner.close()
                    runner, loaded_key = None, None
        finally:
            # 不在采集线程 close 子进程。退出时与新提交原子交接，避免丢唤醒。
            if runner is not None:
                runner.close()
            with self._cv:
                for owner in self._owners.values():
                    owner._idle()
                self._thread = None
                if self._pending:
                    self._thread = threading.Thread(target=self._run, name="action-shadow", daemon=True)
                    self._thread.start()


_scheduler: ActionScheduler | None = None
_scheduler_lock = threading.Lock()


def shared_scheduler() -> ActionScheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = ActionScheduler()
        return _scheduler


class ActionShadow:
    def __init__(self, cfg: ActionShadowCfg, station: str, base_dir: Path,
                 scheduler: ActionScheduler | None = None, clock: Callable = time.monotonic):
        self.cfg = cfg.model_copy(deep=True)
        self.station, self.clock, self.key = station, clock, uuid.uuid4().hex
        self.model = Path(cfg.model)
        if not self.model.is_absolute():
            self.model = base_dir / self.model
        self._lock = threading.RLock()
        self._ring: deque = deque(maxlen=FRAMES)
        self._identity: tuple | None = None
        self._epoch = self._serial = 0
        self._last_sample = self._last_submit = -math.inf
        self._last_ts: float | None = None
        self._last_observed: float | None = None
        self._completion = self._result = None
        self._pending_id = None
        self._inflight = self._closed = self._warm = False
        self._worker = "cold"
        self._status, self._reason = "disabled", "disabled"
        self._metrics = {"submitted": 0, "replaced": 0, "accepted": 0, "discarded": 0, "errors": 0,
                         "cold_starts": 0, "queue_wait_ms": None, "last_frame_to_result_ms": None,
                         "inference_ms": None}
        self.scheduler: ActionScheduler | None = None
        if not cfg.enabled:
            return
        self._status, self._reason = "unavailable", "model_missing"
        if not cfg.model or self.model.suffix.lower() != ".onnx" or not self.model.is_file():
            return
        if not cfg.class_names:
            self._reason = "classes_missing"
            return
        self.scheduler = scheduler or shared_scheduler()
        if not self.scheduler.register(self):
            self.scheduler = None
            self._reason = "station_capacity"
            return
        self._status, self._reason = "unknown", "warming_window"

    def invalidate(self, reason: str = "invalidated") -> None:
        with self._lock:
            self._epoch += 1
            self._ring.clear()
            self._result = self._completion = self._pending_id = None
            self._last_sample = self._last_submit = -math.inf
            self._last_ts = None
            if self.scheduler is not None and not self._closed:
                self._status, self._reason = "unknown", reason
        if self.scheduler is not None:
            self.scheduler.discard(self)

    def observe(self, frame: np.ndarray, ts: float, *, generation: int, cycle_id: str | None,
                object_id: str | None, quality_ok: bool, visible: bool = True, trigger: bool = True) -> None:
        if self.scheduler is None or self._closed:
            return
        now = self.clock()
        identity = (generation, cycle_id, object_id or "")
        with self._lock:
            self._last_observed = now
        if not math.isfinite(ts) or not quality_ok or not visible or not object_id:
            self.invalidate("quality_unknown" if not quality_ok else "occluded_or_untracked")
            return
        with self._lock:
            changed = identity != self._identity
            discontinuity = (self._last_ts is not None and (ts <= self._last_ts or ts - self._last_ts > self.cfg.max_gap_ms / 1000))
            gap = self._last_sample != -math.inf and now - self._last_sample > self.cfg.max_gap_ms / 1000
        if changed or discontinuity or gap:
            self.invalidate("identity_changed" if changed else "frame_gap")
        if not trigger:
            # 退出触发窗口立即撤销旧任务；保留过去帧供下次触发预缓存。
            # epoch 使正在推理、无法从 pending 撤回的结果同样失效。
            with self._lock:
                self._epoch += 1
                self._result = self._completion = self._pending_id = None
                self._status, self._reason = "unknown", "outside_step_window"
            self.scheduler.discard(self)
        with self._lock:
            self._identity, self._last_ts = identity, ts
            self._drain(now)
            if now - self._last_sample + 1e-9 < 1 / SAMPLE_HZ:
                return
        try:
            from .action_predict import prepare_frame
            x0, y0, x1, y1 = self.cfg.roi
            h, w = frame.shape[:2]
            crop = frame[int(y0*h):int(math.ceil(y1*h)), int(x0*w):int(math.ceil(x1*w))]
            if crop.size == 0 or frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
                raise ValueError("invalid_frame")
            sample = prepare_frame(np.ascontiguousarray(crop[:, :, ::-1]))
        except Exception:
            self.invalidate("invalid_frame")
            return
        with self._lock:
            self._last_sample = now
            self._ring.append((ts, now, sample))
            if len(self._ring) < FRAMES:
                if self._result is None:
                    self._status, self._reason = "buffering", "warming_window"
                return
            if not trigger:
                self._result = None
                self._status, self._reason = "unknown", "outside_step_window"
                return
            if now - self._last_submit + 1e-9 < SUBMIT_S:
                return
            self._serial += 1
            window = Window(self._serial, self._epoch, identity, self._ring[0][0], ts, now, now,
                            np.stack([f[2] for f in self._ring]))
            self._pending_id, self._last_submit = window.id, now
            self._metrics["submitted"] += 1
            self._status, self._reason = "queued", ""
        replaced = self.scheduler.submit(self, window)
        if replaced:
            with self._lock:
                self._metrics["replaced"] += 1

    def _started(self, window: Window, cold: bool) -> None:
        with self._lock:
            self._inflight = True
            if self._pending_id == window.id:
                self._pending_id = None
            self._worker = "cold_start" if cold else "warm"
            self._warm = not cold
            self._metrics["cold_starts"] += int(cold)

    def _complete(self, window: Window, out: dict | None, error: str, wait_ms: float, elapsed_ms: float) -> None:
        with self._lock:
            if self._closed:
                return
            self._inflight = False
            if self._pending_id == window.id:
                self._pending_id = None
            self._warm = not bool(error)
            self._worker = "warm" if self._warm else "cold"
            self._completion = (window, out, error)
            self._metrics.update(queue_wait_ms=wait_ms, inference_ms=elapsed_ms,
                                 last_frame_to_result_ms=max(0., (self.clock() - window.end_mono) * 1000))

    def _idle(self) -> None:
        with self._lock:
            self._warm, self._worker = False, "cold"

    def _drain(self, now: float) -> None:
        if self._result is not None and now > self._result["expires_mono"]:
            self._result = None
            self._status, self._reason = "unknown", "expired"
        if self._completion is None:
            return
        window, out, error = self._completion
        self._completion = None
        if window.epoch != self._epoch or window.identity != self._identity or now - window.end_mono > self.cfg.ttl_ms / 1000:
            self._metrics["discarded"] += 1
            self._result = None
            self._status, self._reason = "unknown", "stale_result"
            return
        try:
            if error:
                raise ValueError(error)
            confidence = float(out["confidence"])
            label, digest = out["label"], out["model_sha256"]
            if label not in self.cfg.class_names or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("invalid_output")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                raise ValueError("invalid_model_hash")
        except (TypeError, ValueError, KeyError):
            self._metrics["errors"] += 1
            self._result = None
            self._status, self._reason = "unknown", "model_error" if error else "invalid_output"
            return
        if confidence < self.cfg.min_conf:
            self._result = None
            self._status, self._reason = "unknown", "low_confidence"
            return
        self._metrics["accepted"] += 1
        self._result = {"label": label, "confidence": confidence, "model_sha256": digest,
                        "window_id": window.id, "start_ts": window.start_ts, "end_ts": window.end_ts,
                        "generation": window.identity[0], "cycle_id": window.identity[1], "object_id": window.identity[2],
                        "expires_mono": window.end_mono + self.cfg.ttl_ms / 1000}
        self._status, self._reason = "ready", ""

    def snapshot(self) -> dict[str, Any]:
        # API线程只读；新推理结果只允许采集线程observe核验后接纳。
        with self._lock:
            result = self._result
            status, reason = self._status, self._reason
            now = self.clock()
            if result and now > result["expires_mono"]:
                result, status, reason = None, "unknown", "expired"
            if self.scheduler and not self._closed and self._last_observed is not None and now - self._last_observed > self.cfg.max_gap_ms / 1000:
                result, status, reason = None, "unknown", "stream_gap"
            return {"enabled": self.cfg.enabled, "mode": "shadow", "status": status, "reason": reason,
                    "buffer_frames": len(self._ring), "buffer_limit": FRAMES, "sample_hz": SAMPLE_HZ,
                    "pending": self._pending_id is not None, "inflight": self._inflight,
                    "worker": self._worker, "warm": self._warm,
                    "result": {k: v for k, v in result.items() if k != "expires_mono"} if result else None,
                    "metrics": dict(self._metrics)}

    def close(self) -> None:
        self.invalidate("closed")
        with self._lock:
            self._closed = True
            self._status, self._reason = "closed", "closed"
        if self.scheduler is not None:
            self.scheduler.discard(self, unregister=True)
