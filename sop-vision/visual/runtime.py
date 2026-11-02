"""工位运行时：取帧 -> 感知 -> 原子事件 -> SOP 引擎 -> 证据 / 推送。"""
from __future__ import annotations

import base64
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from .dsl import Program, StationConfig
from .engine import Engine
from .events import OBJ_STATE, Event
from .evidence import CycleStore, EvidenceRecorder
from .llm_provider import ProviderConfig, VLMProvider
from .perception.base import Context, build_geoms
from .perception.hands import HandZoneTracker, MediaPipeHands
from .perception.objects import MediaPipeObjectDetector
from .perception.persons import MediaPipePosePersons
from .perception.quality import QualityPerceiver
from .perception.roi_state import RoiStatePerceiver
from .safety import SafetyMonitor, SafetyRule
from .sim import SimHands, SimSource
from .sources import CvSource

STATUS_COLOR = {"pending": (150, 150, 150), "active": (193, 198, 62), "done": (141, 197, 89)}
# 与界面令牌同色系（BGR）：主色青、成功绿、预警琥珀、空闲灰蓝
COL_ACCENT, COL_OK, COL_HAND, COL_IDLE = (193, 198, 62), (141, 197, 89), (82, 172, 216), (176, 168, 158)


class StationRuntime:
    def __init__(self, cfg: StationConfig, data_dir: Path, base_dir: Path | None = None, hands_override=None,
                 store: CycleStore | None = None):
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
        self.alerts: list[dict[str, Any]] = []
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []
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
        self.source: Any
        self.hands: Any = None
        if src.type == "sim":
            self.source = SimSource(cfg)
            self.hands = SimHands(self.source.scene, cfg)
            for zid in self.roi.trackers:
                self.roi.trackers[zid].set_reference(self.source.scene.reference(zid))
        else:
            self.source = CvSource(src.type, src.uri, src.fps, src.realtime, src.loop, (src.width, src.height))
            if cfg.hands.enabled:
                self.hands = hands_override or MediaPipeHands(cfg.hands, self.geoms, self.base_dir)
        # 人员感知(安全规则/vlm 无关,仅真实源;sim 无人员真值)
        self.persons: Any = None
        if cfg.persons.enabled and src.type != "sim":
            self.persons = MediaPipePosePersons(cfg.persons, self.geoms, self.base_dir)
        self.detector: Any = None
        if cfg.detector.enabled and src.type != "sim":
            self.detector = MediaPipeObjectDetector(cfg.detector, self.geoms, self.base_dir)
        # 常驻安全规则(S16/S17):独立于周期,任何时刻持续监控
        rules = [SafetyRule(id=r.id, kind=r.kind, zone=r.zone, min_s=r.min_s, count_ge=r.count_ge,
                            severity=r.severity, name=r.name) for r in cfg.safety.rules]
        self.safety = SafetyMonitor(rules) if rules else None
        # vlm_check:后台线程判帧,不阻塞视频循环;结果以 OBJ_STATE(伪区域)进引擎
        self._vlm_conds = {}
        for st in self.program.steps:
            for c in st.evidence.conds:  # CompiledStep 字段平铺,无 .spec
                if c.kind == "vlm_check":
                    self._vlm_conds[c.vlm_check.key] = c.vlm_check
        self._vlm_provider: VLMProvider | None = None
        self._vlm_q: queue.Queue = queue.Queue()
        self._vlm_results: deque[tuple[str, bool]] = deque()
        self._vlm_last: dict[str, float] = {}
        self._vlm_ok: set[str] = set()
        self._vlm_thread: threading.Thread | None = None
        if self._vlm_conds and ProviderConfig.from_env().api_key:
            self._vlm_provider = VLMProvider(ProviderConfig.from_env())
            self._vlm_thread = threading.Thread(target=self._vlm_worker, name=f"vlm-{cfg.station}", daemon=True)
            self._vlm_thread.start()
        self.recorder = EvidenceRecorder(data_dir / "evidence", self.store, lambda: self.annotated,
                                         {"hands": "mediapipe-hand-landmarker" if src.type != "sim" else "sim",
                                          "roi_state": "opencv-roi@1"})
        self.engine.subscribe(self.recorder.on_event)
        self.engine.subscribe(self._on_engine_event)

    # ------------------------------------------------------------ 事件分发
    def _on_engine_event(self, kind: str, p: dict[str, Any]) -> None:
        if kind == "cycle_start":
            self._vlm_ok.clear()  # 新周期重新判定 vlm 条件
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
        while not self.stop_event.is_set():
            try:
                item = self._vlm_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                return
            key, prompt, jpeg = item
            b64 = base64.b64encode(jpeg).decode()
            out = self._vlm_provider.inspect([b64], self._vlm_prompt(prompt))
            self._vlm_results.append((key, bool(out["ok"] and str(out["verdict"]).lower() in ("yes", "true", "pass"))))

    def _vlm_drain(self, ts: float, frame: np.ndarray) -> None:
        """收判定结果(真→写伪区域状态),并按抽帧间隔把激活步骤的 vlm 条件送判。"""
        while self._vlm_results:
            key, yes = self._vlm_results.popleft()
            if yes:
                self._vlm_ok.add(key)
                self.engine.process(Event(ts, OBJ_STATE, zone=key, state="ok"))
        if self._vlm_provider is None or not self.engine.cycle:
            return
        active_keys = {c.vlm_check.key for st in self.engine.cycle.steps if st.status == "ACTIVE"
                       for c in st.spec.evidence.conds if c.kind == "vlm_check"}
        ok, buf = cv2.imencode(".jpg", self.raw, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return
        for key, vc in self._vlm_conds.items():
            if key not in active_keys or key in self._vlm_ok or self._vlm_q.qsize() > 4:
                continue
            if ts - self._vlm_last.get(key, -1e9) < vc.every_s:
                continue
            self._vlm_last[key] = ts
            self._vlm_q.put((key, vc.prompt, buf.tobytes()))

    # ------------------------------------------------------------ 主循环
    def process_frame(self, ts: float, frame: np.ndarray) -> list[Event]:
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
        events += self.quality.process(frame, ts, self.ctx)
        for ev in sorted(events, key=lambda e: e.ts):
            self.engine.process(ev)
            if self.safety is not None:
                self.safety.process(ev)
        self.engine.tick(ts)
        if self.safety is not None:
            for alert in self.safety.tick(ts):
                self.alerts.insert(0, {"station": self.cfg.station, "cycle_id": None,
                                       "at": int(time.time() * 1000), **alert})
                del self.alerts[50:]
                for fn in self.listeners:
                    fn("safety", alert)
        self._vlm_drain(ts, frame)
        self.annotated = self._draw(frame, hud=True)
        self.stream = self._draw(frame, hud=False)
        self.recorder.flush()
        self.frame_idx += 1
        if time.monotonic() - self._state_wall > 0.25:  # 状态快照在本线程生成，API 线程只读缓存，避免读写竞争
            self._state_wall = time.monotonic()
            self.state_cache = self.state()
        return events

    def run(self, max_frames: int | None = None) -> None:
        pace = self.cfg.source.type == "sim" and self.cfg.source.realtime
        t0 = time.monotonic()
        while not self.stop_event.is_set():
            item = self.source.read()
            if item is None:
                break
            ts, frame = item
            if frame.shape[1] != self.w or frame.shape[0] != self.h:
                if frame.shape[1] < 16:  # 断流占位帧
                    continue
                frame = cv2.resize(frame, (self.w, self.h))
            self.process_frame(ts, frame)
            if pace:
                delay = t0 + ts - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            if max_frames and self.frame_idx >= max_frames:
                break
        self.source.close()

    def stop(self) -> None:
        self.stop_event.set()
        if self._vlm_thread is not None:
            self._vlm_q.put(None)
            self._vlm_thread.join(timeout=2)

    def close(self) -> None:
        self.stop_event.set()
        if self._owns_store:
            self.store.close()

    # ------------------------------------------------------------ 状态 / 画面
    def meta(self) -> dict[str, Any]:
        c = self.cfg
        return {"id": c.station, "name": c.name or c.station, "line": c.line, "source": c.source.type,
                "takt_s": c.cycle.takt_s, "rules_hash": self.program.rules_hash,
                "width": self.w, "height": self.h}

    def state(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
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
