"""工位运行时：取帧 -> 感知 -> 原子事件 -> SOP 引擎 -> 证据 / 推送。"""
from __future__ import annotations

import base64
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from .action_shadow import ActionShadow
from .dsl import Program, StationConfig
from .engine import Engine
from .events import OBJ_STATE, QUALITY, Event
from .evidence import CycleStore, EvidenceRecorder
from .llm_provider import ProviderConfig, VLMProvider
from .perception.base import Context, build_geoms
from .perception.hands import HandZoneTracker, MediaPipeHands
from .perception.objects import MediaPipeObjectDetector
from .perception.persons import MediaPipePosePersons
from .perception.quality import QualityPerceiver
from .perception.roi_state import RoiStatePerceiver
from .safety import SafetyMonitor, SafetyRule
from .ehs import EhsMonitor
from .proximity import ProximityMonitor
from .ppe import PPEMonitor
from .helmet_shadow import HelmetShadow, HelmetSampleMonitor
from .thermal import ThermalMonitor
from .sim import SimHands, SimSource
from .sources import create_source

STATUS_COLOR = {"pending": (150, 150, 150), "active": (193, 198, 62), "done": (141, 197, 89)}
# 与界面令牌同色系（BGR）：主色青、成功绿、预警琥珀、空闲灰蓝
COL_ACCENT, COL_OK, COL_HAND, COL_IDLE = (193, 198, 62), (141, 197, 89), (82, 172, 216), (176, 168, 158)


class StationRuntime:
    def __init__(self, cfg: StationConfig, data_dir: Path, base_dir: Path | None = None, hands_override=None,
                 store: CycleStore | None = None, action_ledger=None):
        self.cfg = cfg
        self.data_dir = data_dir
        self.base_dir = base_dir or Path.cwd()
        src = cfg.source
        self.w, self.h = src.width, src.height
        self.program = Program.from_config(cfg)
        self.engine = Engine(self.program, epoch_offset=time.time())
        self.geoms = build_geoms(cfg.zones, self.w, self.h)
        self.ctx = Context(self.geoms)
        self._owns_store = store is None
        self.store = store or CycleStore(data_dir / "sop.db")
        self.raw: np.ndarray | None = None
        self.annotated: np.ndarray | None = None
        self.stream: np.ndarray | None = None
        self.frame_idx = 0
        self._health_epoch = uuid.uuid4().hex
        self._health_running = False
        self._health_errors = 0
        self._health_last_frame = None
        self.alerts: list[dict[str, Any]] = []
        self._alerts_lock = threading.Lock()
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self.frame_observers: list[Callable] = []
        self._cross_camera_frame = None
        self.signal_inbox = None
        self.thermal_service = None
        self._recent_signals: list[dict[str, Any]] = []
        self._last_frame_wall: float | None = None
        self._calib_request: list[str] | None = None
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        self.state_cache: dict[str, Any] = {}
        self._state_wall = 0.0
        self._stats_cache: tuple[float, dict[str, Any]] = (0.0, {})
        self._jpeg_cache: tuple[int, bytes | None] = (-1, None)

        ref_dir = data_dir / "refs" / cfg.station
        self.roi = RoiStatePerceiver(cfg.roi_states, self.geoms, ref_dir)
        self.quality = QualityPerceiver(cfg.quality)
        self._quality_blocked = False
        self._vision_generation = 0
        self.action_shadow = ActionShadow(cfg.action_shadow, cfg.station, self.base_dir)
        # 影子结果与步骤期望动作的对账账本；None 或未配置映射时跳过。
        self.action_ledger = action_ledger
        self._ledger_window: int | None = None
        self._action_box = None
        self._action_token = 0
        self.source: Any
        self.hands: Any = None
        if src.type == "sim":
            self.source = SimSource(cfg)
            self.hands = SimHands(self.source.scene, cfg)
            for zid in self.roi.trackers:
                self.roi.trackers[zid].set_reference(self.source.scene.reference(zid))
        else:
            self.source = create_source(src, stop_event=self.stop_event, base_dir=self.base_dir)
            if cfg.hands.enabled:
                self.hands = hands_override or MediaPipeHands(cfg.hands, self.geoms, self.base_dir)
        # 人员感知(安全规则/vlm 无关,仅真实源;sim 无人员真值)
        self.persons: Any = None
        if cfg.persons.enabled and src.type != "sim":
            self.persons = MediaPipePosePersons(cfg.persons, self.geoms, self.base_dir)
        self.detector: Any = None
        if cfg.detector.enabled and src.type != "sim":
            from .onnx_detector import create_detector
            self.detector = create_detector(cfg.detector, self.geoms, self.base_dir)
        # 常驻安全规则(S16/S17):独立于周期,任何时刻持续监控
        rules = [SafetyRule(id=r.id, kind=r.kind, zone=r.zone, min_s=r.min_s, count_ge=r.count_ge,
                            severity=r.severity, name=r.name, target=r.target) for r in cfg.safety.rules]
        self.safety = SafetyMonitor(rules) if cfg.safety.enabled and rules else None
        self.ehs = EhsMonitor(cfg.ehs, self.geoms) if cfg.ehs.enabled else None
        self.proximity = ProximityMonitor(cfg.proximity, (self.w, self.h))
        self.ppe = PPEMonitor(cfg.ppe, self.geoms) if cfg.ppe.enabled else None
        self.helmet_shadow = None
        self.helmet_samples = None
        self._helmet_candidate = {"enabled": False, "status": "disabled", "close_complete": None}
        if cfg.ppe.enabled and cfg.ppe.helmet_probe.enabled:
            ttl = max(.05, min(5., cfg.ppe.max_gap_s))
            self.helmet_shadow = HelmetShadow(cfg.ppe.helmet_probe, self.base_dir,
                                             ttl_s=ttl, idle_s=cfg.ppe.helmet_probe.idle_s)
            self.helmet_samples = HelmetSampleMonitor(cfg.ppe, self.geoms, ttl_s=ttl)
            self._helmet_candidate = {"enabled": True, "status": "idle", "close_complete": None}

        self.thermal = ThermalMonitor(cfg.thermal)
        # vlm_check:后台线程判帧,不阻塞视频循环;结果以 OBJ_STATE(伪区域)进引擎
        self._vlm_conds = {}
        for st in self.program.steps:
            for c in st.evidence.conds:  # CompiledStep 字段平铺,无 .spec
                if c.kind == "vlm_check":
                    self._vlm_conds[c.vlm_check.key] = c.vlm_check
        self._vlm_provider: VLMProvider | None = None
        self._vlm_q: queue.Queue = queue.Queue()
        self._vlm_results: deque[tuple[int, str, bool]] = deque()
        self._vlm_last: dict[str, float] = {}
        self._vlm_ok: set[str] = set()
        self._vlm_thread: threading.Thread | None = None
        self._vlm_guard = threading.Lock()
        if self._vlm_conds:
            provider_cfg = ProviderConfig.from_env()
            if provider_cfg.configured:
                self._vlm_provider = VLMProvider(provider_cfg)

        self.recorder = EvidenceRecorder(data_dir / "evidence", self.store, lambda: self.annotated,
                                         {"hands": "mediapipe-hand-landmarker" if src.type != "sim" else "sim",
                                          "roi_state": "opencv-roi@1"})
        self.engine.subscribe(self.recorder.on_event)
        self.engine.subscribe(self._on_engine_event)

    # ------------------------------------------------------------ 事件分发
    def _on_engine_event(self, kind: str, p: dict[str, Any]) -> None:
        if kind == "cycle_start":
            self._vlm_ok.clear()  # 新周期重新判定 vlm 条件
            self._vision_generation += 1
            self._vlm_results.clear()
        if kind == "violation":
            self.alerts.insert(0, {"station": self.cfg.station, "cycle_id": p["cycle_id"], "at": int(time.time() * 1000),
                                   **{k: p[k] for k in ("kind", "severity", "step", "message")}})
            del self.alerts[50:]
        for fn in self.listeners:
            fn(kind, p)

    # ------------------------------------------------------------ 标定
    def request_calibration(self, zones: list[str] | None = None) -> None:
        with self._lock:
            self._calib_request = zones if zones is not None else list(self.roi.trackers)

    def _apply_calibration(self, frame: np.ndarray) -> None:
        with self._lock:
            req, self._calib_request = self._calib_request, None
        if req:
            done = self.roi.calibrate(frame, req)
            if self.cfg.quality.enabled:
                self.quality.set_reference(frame, self.ctx)
            for fn in self.listeners:
                fn("calibrated", {"zones": done})

    # ------------------------------------------------------------ vlm_check 语言层判定
    @staticmethod
    def _vlm_prompt(prompt: str) -> str:
        return (f'判断画面是否满足条件:「{prompt}」。只输出JSON:'
                '{"verdict":"yes|no|unclear","confidence":0到1的小数,"reason":"不超过20字"}')

    def _vlm_worker(self) -> None:
        while True:
            with self._vlm_guard:
                if self.stop_event.is_set():
                    self._vlm_thread = None
                    return
                try:
                    item = self._vlm_q.get_nowait()
                except queue.Empty:
                    self._vlm_thread = None
                    return
            if item is None:
                continue
            generation, key, prompt, jpeg = item
            if generation != self._vision_generation:
                continue
            try:
                b64 = base64.b64encode(jpeg).decode()
                out = self._vlm_provider.inspect([b64], self._vlm_prompt(prompt))
                yes = bool(out["ok"] and str(out["verdict"]).lower() in ("yes", "true", "pass"))
            except Exception:
                yes = False
            self._vlm_results.append((generation, key, yes))

    def _vlm_drain(self, ts: float, frame: np.ndarray) -> None:
        """收判定结果(真→写伪区域状态),并按抽帧间隔把激活步骤的 vlm 条件送判。"""
        while self._vlm_results:
            generation, key, yes = self._vlm_results.popleft()
            if generation == self._vision_generation and yes:
                self._vlm_ok.add(key)
                self.engine.process(Event(ts, OBJ_STATE, zone=key, state="ok"))
        if self._vlm_provider is None or not self.engine.cycle:
            return
        if getattr(self._vlm_provider, 'ready', True) is False:
            return
        active_keys = {c.vlm_check.key for st in self.engine.cycle.steps if st.status == "active"
                       for c in st.spec.evidence.conds if c.kind == "vlm_check"}
        due = [(key, vc) for key, vc in self._vlm_conds.items()
               if key in active_keys and key not in self._vlm_ok
               and ts - self._vlm_last.get(key, -1e9) >= vc.every_s]
        if not due or self._vlm_q.qsize() >= 4 or self.stop_event.is_set():
            return
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return
        with self._vlm_guard:
            if self.stop_event.is_set():
                return
            for key, vc in due:
                if self._vlm_q.qsize() >= 4:
                    break
                self._vlm_last[key] = ts
                self._vlm_q.put((self._vision_generation, key, vc.prompt, buf.tobytes()))
            if self._vlm_thread is None and not self._vlm_q.empty():
                self._vlm_thread = threading.Thread(target=self._vlm_worker, name=f"vlm-{self.cfg.station}", daemon=True)
                self._vlm_thread.start()

    def _action_observe(self, ts: float, frame: np.ndarray, blocked: bool) -> None:
        cfg = self.cfg.action_shadow
        if not cfg.enabled:
            return
        boxes = self.ctx.person_boxes
        zone = self.geoms.get(cfg.trigger_zone)
        if zone:
            boxes = [b for b in boxes if zone.contains((b[0]+b[2])/2, (b[1]+b[3])/2)]
        # 仅单人框连续性令牌，不是持久身份识别；多人、遮挡或跳变均清窗。
        if blocked or len(boxes) != 1:
            self._action_box = None
            token = None
        else:
            box = tuple(boxes[0])
            prev = self._action_box
            overlap = 0.
            if prev is not None:
                area = max(0., min(box[2], prev[2])-max(box[0], prev[0])) * max(0., min(box[3], prev[3])-max(box[1], prev[1]))
                union = (box[2]-box[0])*(box[3]-box[1]) + (prev[2]-prev[0])*(prev[3]-prev[1]) - area
                overlap = area / union if union > 0 else 0.
            if overlap < .5:
                self._action_token += 1
            self._action_box = box
            token = f"single-person-{self._action_token}"
        cycle = self.engine.cycle
        active = {st.spec.id for st in cycle.steps if st.status == "active"} if cycle else set()
        trigger = not cfg.active_steps or bool(active.intersection(cfg.active_steps))
        self.action_shadow.observe(frame, ts, generation=self._vision_generation,
                                   cycle_id=cycle.id if cycle else None, object_id=token,
                                   quality_ok=not blocked, visible=token is not None, trigger=trigger)
        self._action_reconcile(cfg, cycle, active, ts)

    def _remember_signals(self, events) -> None:
        """保留最近 30 秒设备信号，供动作对账作为同窗口证据；不参与规则判定。"""
        now = time.time()
        for event in events:
            data = getattr(event, "data", None) or {}
            self._recent_signals.append({
                "name": getattr(event, "name", None),
                "value": getattr(event, "value", None),
                "source": getattr(event, "source", None),
                "quality": data.get("quality"),
                "observed_at": data.get("observed_at"),
                "received_at": data.get("received_at"),
            })
        # 用服务端接收时刻算窗口,避免设备时钟偏差把有效证据过滤掉。
        self._recent_signals = [
            item for item in self._recent_signals
            if now - float(item.get("received_at") or item.get("observed_at") or now) <= 30
        ][-20:]

    def _action_reconcile(self, cfg, cycle, active, ts: float) -> None:
        """影子结果与步骤期望动作对账；只记账不判定，任何失败都不阻塞采集线程。"""
        if self.action_ledger is None or not cfg.step_actions:
            return
        try:
            result = self.action_shadow.snapshot().get("result")
            if not result or result.get("window_id") == self._ledger_window:
                return
            if cycle is None or result.get("cycle_id") != cycle.id:
                return
            expected = sorted({a for sid in active for a in cfg.step_actions.get(sid, [])
                               if a in cfg.class_names})
            evidence = {"signals": list(getattr(self, "_recent_signals", []))} if getattr(self, "_recent_signals", None) else None
            row = self.action_ledger.record(
                self.cfg.station, cycle.id, sorted(active), expected,
                result["label"], result["confidence"], result["model_sha256"], result["window_id"],
                evidence=evidence)
            if row is not None:
                self._ledger_window = result["window_id"]
        except Exception:
            self._ledger_window = None

    def bind_calibrations(self, store):
        self.proximity.bind_calibrations(store)

    def bind_signal_inbox(self, inbox):
        """Thermal input bypasses camera reads; SOP keeps its existing frame queue."""
        self.signal_inbox = inbox
        if self.cfg.thermal.enabled:
            from .thermal_service import ThermalSignalService
            self.thermal_service = ThermalSignalService(self.cfg.thermal, self._emit_safety)
            inbox.bind_observer(self.thermal_service.observe, self.thermal_service.keys)

    def _emit_safety(self, alert):
        with self._alerts_lock:
            self.alerts.insert(0, {"station": self.cfg.station, "cycle_id": None,
                                  "at": int(time.time() * 1000), **alert})
            del self.alerts[50:]
        for fn in self.listeners:
            fn("safety", alert)

    def public_state(self):
        """Refresh device TTL without reading the camera-owned engine from HTTP."""
        snap = dict(self.state_cache) if self.state_cache else {"meta": self.meta()}
        if self.thermal_service is not None:
            snap["thermal"] = self.thermal_service.snapshot()
            with self._alerts_lock:
                snap["alerts"] = self.alerts[:20]
        return snap

    def bind_ai_services(self, store):
        """SOP and snapshot inspection share the same configured VLM binding."""
        if self._vlm_conds:
            from .ai_services import DynamicAIProvider
            fallback = self._vlm_provider or VLMProvider(ProviderConfig.from_env())
            self._vlm_provider = DynamicAIProvider(store, fallback)

    def _reset_visual_observations(self) -> None:
        """只清观测/去抖缓存，保留模型、标定参考和原始帧。"""
        self._cross_camera_frame = None
        if self.ehs is not None:
            self.ehs.reset()
        self.proximity.reset()
        if self.ppe is not None:
            self.ppe.reset()
        if self.helmet_shadow is not None:
            self.helmet_shadow.invalidate()
            self.helmet_samples.reset()
            self._helmet_candidate = {"enabled": True, "status": "unknown", "close_complete": None}
        for perceiver in (self.hands, self.persons, self.detector, self.roi):
            if perceiver is None:
                continue
            reset = getattr(perceiver, "reset_observations", None)
            if reset is None:
                reset = getattr(getattr(perceiver, "tracker", None), "reset", None)
            if reset is not None:
                reset()
        self._vlm_results.clear()
        self._vlm_ok.clear()
        self._action_box = None
        self.action_shadow.invalidate("observations_reset")

    def _quality_gate(self, ts: float) -> tuple[bool, Event | None]:
        observation = self.quality.snapshot()
        blocked = self.cfg.quality.enabled and (self.quality.degraded or observation["observation"] in {"bad", "unknown"})
        reason = (observation.get("raw_reason") or self.quality.reason or "quality_unknown") if blocked else ""
        event = None
        if blocked != self.engine.world.quality_bad or (blocked and reason != self.engine.world.quality_reason):
            event = Event(ts, QUALITY, state="degraded" if blocked else "ok", value=reason, source="quality_gate",
                          data={"confirmed": self.quality.degraded, "observation": observation["observation"]})
            self.engine.process(event)  # 必须先于同帧的任何视觉证据
        if blocked:
            if not self._quality_blocked:
                self._vision_generation += 1  # 丢弃降级前送判但恢复后才返回的旧 VLM 结果
                self._vlm_last.clear()
                while True:
                    try:
                        self._vlm_q.get_nowait()
                    except queue.Empty:
                        break
            self._reset_visual_observations()
            if self.safety is not None:
                self.safety.pause(ts)
        elif self.safety is not None:
            self.safety.resume(ts)  # 恢复只解除暂停，不能恢复旧计时/人数
        self._quality_blocked = blocked
        return blocked, event

    # ------------------------------------------------------------ 主循环
    def process_frame(self, ts: float, frame: np.ndarray) -> list[Event]:
        captured_mono = time.monotonic()
        detector_fresh = self.detector is not None and getattr(self.detector, "_last_frame_ts", None) != ts
        external = self.signal_inbox.drain(ts) if self.signal_inbox is not None else []
        if external:
            self._remember_signals(external)
        self.raw = frame
        self._apply_calibration(frame)
        self.ctx.reset()
        events: list[Event] = []
        if self.hands is not None:
            events += self.hands.process(frame, ts, self.ctx)
        if self.persons is not None:
            events += self.persons.process(frame, ts, self.ctx)
        if self.detector is not None:
            events += self.detector.process(frame, ts, self.ctx)
        events += self.roi.process(frame, ts, self.ctx)
        quality_events = self.quality.process(frame, ts, self.ctx)
        blocked, gate_event = self._quality_gate(ts)
        # Device temperatures remain independent of camera lighting quality.
        safety_alerts = [] if self.thermal_service is not None else self.thermal.process(external, ts)
        frame_events = ([] if blocked else sorted(events, key=lambda e: e.ts)) + external
        self.engine.process_batch(frame_events, ts)
        if not blocked:
            for ev in sorted(events, key=lambda e: e.ts):
                if self.safety is not None:
                    safety_alerts += self.safety.process(ev)
        if self.safety is not None and not blocked:
            safety_alerts += self.safety.tick(ts)
        if self.ppe is not None and not blocked:
            safety_alerts += self.ppe.process(self.ctx.object_tracks, ts,
                available=self.detector is not None and hasattr(self.detector, "mot"))
        if self.helmet_shadow is not None and not blocked:
            self.helmet_shadow.submit(frame, ts, self.ctx.object_tracks, self._vision_generation,
                                      captured_mono=captured_mono)
            candidate = self.helmet_shadow.drain(self._vision_generation, ts, self.ctx.object_tracks)
            safety_alerts += self.helmet_samples.update(candidate, self._vision_generation, ts, self.ctx.object_tracks)
            if not any(t.label == "person" and t.last_seen == ts for t in self.ctx.object_tracks):
                self._helmet_candidate = {"enabled": True, "status": "no_person", "close_complete": None}
            if candidate is not None:
                self._helmet_candidate = {"enabled": True, "status": candidate["status"],
                    "frame_ts": candidate["frame_ts"], "captured_mono": candidate["captured_mono"],
                    "sampled_person_ids": candidate["sampled_person_ids"], "close_complete": None}
        if self.ehs is not None and not blocked:
            safety_alerts += self.ehs.process(self.ctx.object_tracks, ts,
                available=self.detector is not None and hasattr(self.detector, "mot"),
                available_labels=set(self.cfg.detector.classes) & set(getattr(self.detector, "class_names", [])))
        if not blocked:
            safety_alerts += self.proximity.process(self.ctx.object_tracks, ts,
                available=self.detector is not None and hasattr(self.detector, "mot"),
                available_labels=set(self.cfg.detector.classes) & set(getattr(self.detector, "class_names", [])),
                frame_size=(frame.shape[1], frame.shape[0]))
        # Visual rules are gated above; device signals still report with a dark camera.
        for alert in safety_alerts:
            self._emit_safety(alert)
        if not blocked:
            self._vlm_drain(ts, frame)
        self._action_observe(ts, frame, blocked)
        self.annotated = self._draw(frame, hud=True)
        self.stream = self._draw(frame, hud=False)
        self.recorder.flush()
        self.frame_idx += 1
        self._health_last_frame = time.time()
        # Publish one detached snapshot; association runs only on API requests.
        self._cross_camera_frame = {
            "station": self.cfg.station, "epoch": self._health_epoch,
            "generation": self._vision_generation, "frame_seq": self.frame_idx,
            "received_mono": captured_mono, "frame_size": (frame.shape[1], frame.shape[0]),
            "available": not blocked and detector_fresh and hasattr(self.detector, "mot"),
            "source_type": self.cfg.source.type,
            "available_labels": tuple(sorted(set(self.cfg.detector.classes) &
                                             set(getattr(self.detector, "class_names", [])))),
            "tracks": tuple({"track_id": t.track_id, "label": t.label,
                             "xyxy": tuple(t.xyxy), "confidence": t.confidence}
                            for t in self.ctx.object_tracks if t.last_seen == ts) if not blocked else (),
        }
        for observer in self.frame_observers:
            observer(self)
        if time.monotonic() - self._state_wall > 0.25:  # 状态快照在本线程生成，API 线程只读缓存，避免读写竞争
            self._state_wall = time.monotonic()
            self.state_cache = self.state()
        return ([] if blocked else events) + external + quality_events + ([gate_event] if gate_event else [])

    def cross_camera_snapshot(self):
        """Host receipt time is not synchronized camera exposure time."""
        snapshot = self._cross_camera_frame
        if snapshot is None:
            return None
        return {**snapshot, "available": snapshot["available"] and self._health_running and not self.stop_event.is_set(),
                "tracks": [dict(track) for track in snapshot["tracks"]]}

    def run(self, max_frames: int | None = None) -> None:
        if self.signal_inbox is not None:
            self.signal_inbox.bind_consumer()
        pace = self.cfg.source.type == "sim" and self.cfg.source.realtime
        t0 = time.monotonic()
        self._health_running = True
        try:
            while not self.stop_event.is_set():
                item = self.source.read()
                if item is None:
                    break
                ts, frame = item
                now = time.monotonic()
                gap = self.cfg.source.type in ("rtsp", "camera", "industrial") and self._last_frame_wall is not None and now - self._last_frame_wall > max(1.0, 3 / max(self.cfg.source.fps, 1))
                if gap or frame.shape[1] < 16:
                    self.engine.process(Event(ts, "quality", state="unknown", value="stream_gap", source="source"))
                    self._vision_generation += 1
                    self._vlm_last.clear()
                    if self.cfg.quality.enabled:
                        self.quality.invalidate("stream_gap", ts)
                    self._reset_visual_observations()
                    if self.safety is not None:
                        self.safety.pause(ts)
                    self._quality_blocked = True
                self._last_frame_wall = now
                if frame.shape[1] != self.w or frame.shape[0] != self.h:
                    if frame.shape[1] < 16:  # 断流占位帧
                        continue
                    frame = cv2.resize(frame, (self.w, self.h))
                self.process_frame(ts, frame)
                if pace:
                    delay = t0 + ts - time.monotonic()
                    if delay > 0:
                        self.stop_event.wait(delay)
                if max_frames and self.frame_idx >= max_frames:
                    break
        except Exception:
            self._health_errors += 1
            raise
        finally:
            self._health_running = False
            try:
                self.source.close()
            finally:
                try:
                    self.action_shadow.close()
                finally:
                    self._close_helmet()

    def _close_helmet(self):
        if self.helmet_shadow is not None:
            complete = self.helmet_shadow.close(timeout_s=.5)
            self.helmet_samples.reset()
            self._helmet_candidate = {"enabled": True,
                "status": "closed" if complete else "closing", "close_complete": complete}

    def stop(self) -> None:
        self.stop_event.set()
        self._close_helmet()
        if self.thermal_service is not None:
            self.signal_inbox.close()
            self.thermal_service.close()
        self.action_shadow.close()
        with self._vlm_guard:
            worker = self._vlm_thread
        if worker is not None:
            worker.join(timeout=2)

    def close(self) -> None:
        self.stop()
        if self._owns_store:
            self.store.close()

    # ------------------------------------------------------------ 状态 / 画面
    def health_snapshot(self) -> dict[str, Any]:
        """Successful-frame timing and worker failures; not accuracy metrics."""
        return {"epoch": self._health_epoch, "running": self._health_running,
                "error_total": self._health_errors, "last_frame_at": self._health_last_frame}

    def meta(self) -> dict[str, Any]:
        c = self.cfg
        return {"id": c.station, "name": c.name or c.station, "line": c.line, "source": c.source.type,
                "takt_s": c.cycle.takt_s if c.cycle else None, "rules_hash": self.program.rules_hash,
                "width": self.w, "height": self.h}

    def state(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
        snap["proximity"] = self.proximity.snapshot()
        snap["thermal"] = self.thermal_service.snapshot() if self.thermal_service is not None else self.thermal.snapshot()
        snap["ppe"] = self.ppe.snapshot() if self.ppe is not None else {"enabled": False}
        helmet = dict(self._helmet_candidate)
        captured = helmet.pop("captured_mono", None)
        if captured is not None:
            age = time.monotonic() - captured
            helmet["age_s"] = round(age, 3)
            if age > self.helmet_shadow.ttl_s:
                helmet.update(status="expired", sampled_person_ids=[])
        helmet["samples"] = (self.helmet_samples.snapshot() if self.helmet_samples is not None
                             and helmet["status"] not in ("expired", "closing", "closed", "unknown") else {})
        snap["helmet_probe"] = helmet
        snap["ehs"] = self.ehs.snapshot() if self.ehs is not None else {"enabled": False, "rules": {}}
        snap["quality"] = {**self.quality.snapshot(), "confirmed_degraded": self.quality.degraded,
                           "confirmed_reason": self.quality.reason, "degraded": self.engine.world.quality_bad,
                           "reason": self.engine.world.quality_reason}
        snap["action_shadow"] = self.action_shadow.snapshot()
        snap["roi"] = self.roi.states()
        snap["alerts"] = self.alerts[:20]
        now = time.monotonic()
        if now - self._stats_cache[0] > 3:
            self._stats_cache = (now, self.store.stats(self.cfg.station, day=datetime.now().strftime("%Y-%m-%d")))
        snap["stats"] = self._stats_cache[1]
        snap["meta"] = self.meta()
        snap["frame"] = self.frame_idx
        return snap

    def _draw(self, frame: np.ndarray, hud: bool) -> np.ndarray:
        """hud=True 用于证据帧（自带步骤状态，脱离界面也能看懂）；hud=False 用于界面视频流（状态由界面面板呈现）。"""
        img = frame.copy()
        states = self.engine.world.zone_state
        for zid, g in self.geoms.items():
            hand_in = zid in self.engine.world.hands
            st = states.get(zid, (None,))[0]
            col = COL_HAND if hand_in else COL_OK if st and st in self._positive_states() else COL_IDLE
            cv2.polylines(img, [g.poly.astype(np.int32)], True, col, 2 if hand_in else 1, cv2.LINE_AA)
            if hud:  # 区域名密集时文字会互相遮挡，界面视频流只画轮廓，状态由面板呈现
                self._label(img, f"{zid}{' · ' + st if st else ''}", (g.bbox[0], max(14, g.bbox[1] - 5)), col)
        for x, y in self.ctx.hand_points:
            cv2.circle(img, (int(x), int(y)), 3, COL_ACCENT, -1, cv2.LINE_AA)
        for box in self.ctx.person_boxes:
            cv2.rectangle(img, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), COL_HAND, 2, cv2.LINE_AA)
        for name, box in self.ctx.object_boxes:
            cv2.rectangle(img, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), COL_ACCENT, 2, cv2.LINE_AA)
            self._label(img, str(name)[:20], (int(box[0]), max(12, int(box[1]) - 4)), COL_ACCENT, 0.42)
        for zid in self.ctx.fall_zones:
            if zid in self.geoms:
                g = self.geoms[zid]
                cv2.polylines(img, [g.poly.astype(np.int32)], True, (80, 80, 240), 3, cv2.LINE_AA)
                self._label(img, "FALL", (g.bbox[0], max(14, g.bbox[1] - 5)), (80, 80, 240))
        if not hud:
            return img
        c = self.engine.cycle
        lines = [(f"cycle #{c.no}  {self.engine.now - c.start_ts:5.1f}s" if c else "idle", (255, 255, 255))]
        if c:
            lines += [(f"{s.spec.id} {s.status}" + (" !order" if s.early else ""), STATUS_COLOR[s.status]) for s in c.steps]
        plate = img.copy()  # 半透明底板，避免状态文字与区域标签叠在一起看不清
        cv2.rectangle(plate, (0, 0), (190, 10 + 17 * len(lines)), (12, 16, 18), -1)
        img = cv2.addWeighted(plate, 0.62, img, 0.38, 0)
        for i, (text, color) in enumerate(lines):
            self._label(img, text, (8, 18 + 17 * i), color, 0.5 if i == 0 else 0.44)
        if self.engine.world.quality_bad:
            self._label(img, f"QUALITY: {self.engine.world.quality_reason}", (8, img.shape[0] - 10), (80, 80, 240), 0.5)
        return img

    @staticmethod
    def _label(img: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int], scale: float = 0.44) -> None:
        """先描深色边再写字，保证在亮 / 暗背景上都清晰。"""
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (12, 16, 18), 3, cv2.LINE_AA)
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)

    def _positive_states(self) -> set[str]:
        return {r.positive_state for r in self.cfg.roi_states}

    def jpeg(self, raw: bool = False) -> bytes | None:
        img = self.raw if raw else self.stream
        if img is None:
            return None
        idx = self.frame_idx
        if not raw and self._jpeg_cache[0] == idx:
            return self._jpeg_cache[1]
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 78])
        data = buf.tobytes() if ok else None
        if not raw:
            self._jpeg_cache = (idx, data)
        return data
