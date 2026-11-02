"""仿真工位：渲染一个合成画面 + 脚本化的操作员，用于无真实视频时端到端验证整条链路。

ROI 状态核验走真实的 OpenCV 管线；手的位置取自仿真真值（真实部署时由 MediaPipe 提供）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .dsl import StationConfig
from .events import Event
from .perception.base import Context, ZoneGeom, build_geoms
from .perception.hands import HandZoneTracker

ITEMS = ("connector_J1", "B1", "B2", "label_zone")


class Scene:
    def __init__(self, cfg: StationConfig, seed: int = 7):
        self.w, self.h = cfg.source.width, cfg.source.height
        self.geoms: dict[str, ZoneGeom] = build_geoms(cfg.zones, self.w, self.h)
        rng = np.random.default_rng(seed)
        bench = np.full((self.h, self.w, 3), 92, np.uint8)
        bench = cv2.add(bench, cv2.GaussianBlur(rng.integers(0, 18, (self.h, self.w, 3), dtype=np.uint8), (15, 15), 0))
        self.bench = bench
        self.on: dict[str, bool] = {k: False for k in ("workpiece", *ITEMS)}
        self.hand: tuple[float, float] | None = None

    def center(self, zone: str) -> tuple[float, float]:
        x0, y0, x1, y1 = self.geoms[zone].bbox
        return (x0 + x1) / 2, (y0 + y1) / 2

    @staticmethod
    def _connector(img: np.ndarray, box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), (52, 52, 58), -1)
        cv2.rectangle(img, (x0 + 3, y0 + 3), (x1 - 4, y1 - 4), (92, 92, 100), 1)
        cols, rows = 6, 2
        for r in range(rows):
            for c in range(cols):
                cx = x0 + 10 + c * max(1, (x1 - x0 - 20) // (cols - 1))
                cy = y0 + (y1 - y0) // 3 * (r + 1)
                cv2.circle(img, (cx, cy), 3, (70, 175, 215), -1)
        cv2.rectangle(img, (x0 + (x1 - x0) // 2 - 8, y0), (x0 + (x1 - x0) // 2 + 8, y0 + 5), (150, 150, 158), -1)

    @staticmethod
    def _label(img: np.ndarray, box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        cv2.rectangle(img, (x0, y0), (x1 - 1, y1 - 1), (238, 238, 236), -1)
        for i, wd in enumerate((0.8, 0.55, 0.7)):
            yy = y0 + 8 + i * 7
            cv2.line(img, (x0 + 6, yy), (x0 + 6 + int((x1 - x0 - 12) * wd), yy), (60, 60, 60), 2)
        for bx in range(x0 + 6, x1 - 8, 4):
            cv2.line(img, (bx, y1 - 22), (bx, y1 - 8), (30, 30, 30), 1 + (bx // 4) % 2)

    def render(self, idx: int, hand: bool = True) -> np.ndarray:
        img = self.bench.copy()
        g = self.geoms
        x0, y0, x1, y1 = g["bin_harness"].bbox
        cv2.rectangle(img, (x0, y0), (x1, y1), (60, 60, 60), -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (86, 86, 86), 2)
        if self.on["workpiece"]:
            x0, y0, x1, y1 = g["fixture"].bbox
            cv2.rectangle(img, (x0, y0), (x1, y1), (172, 164, 154), -1)
            for yy in range(y0, y1, 24):
                cv2.line(img, (x0, yy), (x1, yy), (152, 144, 134), 1)
            cv2.rectangle(img, (x0, y0), (x1, y1), (196, 188, 178), 2)
        for k in ITEMS:
            if k not in g or not self.on[k]:
                continue
            x0, y0, x1, y1 = g[k].bbox
            if k.startswith("B"):
                c = ((x0 + x1) // 2, (y0 + y1) // 2)
                r = max(4, min(x1 - x0, y1 - y0) // 2 - 2)
                cv2.circle(img, c, r, (25, 25, 25), -1)
                cv2.line(img, (c[0] - r + 2, c[1]), (c[0] + r - 2, c[1]), (230, 230, 230), 2)
                cv2.line(img, (c[0], c[1] - r + 2), (c[0], c[1] + r - 2), (230, 230, 230), 2)
            elif k == "label_zone":
                self._label(img, (x0, y0, x1, y1))
            else:
                self._connector(img, (x0, y0, x1, y1))
        if hand and self.hand is not None:
            cv2.circle(img, (int(self.hand[0]), int(self.hand[1])), 28, (120, 150, 215), -1)
        rng = np.random.default_rng(idx)
        noisy = img.astype(np.int16) + rng.normal(0, 2.5, img.shape).astype(np.int16)
        return np.clip(noisy, 0, 255).astype(np.uint8)

    def reference(self, zone: str) -> np.ndarray:
        """标定用参考画面：diff_empty 取空台面，template 取“工件在位 + 该物件在位”。"""
        saved = dict(self.on), self.hand
        self.hand = None
        for k in self.on:
            self.on[k] = False
        if zone != "fixture":
            self.on["workpiece"] = True
            self.on[zone] = True
        frame = self.render(0, hand=False)
        self.on, self.hand = saved[0], saved[1]
        return frame


@dataclass
class Timeline:
    state_changes: list[tuple[float, dict[str, bool]]] = field(default_factory=list)
    hand_kfs: list[tuple[float, tuple[float, float] | None]] = field(default_factory=list)
    end: float = 0.0

    def set(self, t: float, **kw: bool) -> None:
        self.state_changes.append((t, kw))
        self.end = max(self.end, t)

    def hand(self, t: float, pos: tuple[float, float] | None) -> None:
        self.hand_kfs.append((t, pos))
        self.end = max(self.end, t)

    def hand_at(self, t: float) -> tuple[float, float] | None:
        prev = nxt = None
        for kt, p in sorted(self.hand_kfs, key=lambda k: k[0]):
            if kt <= t:
                prev = (kt, p)
            else:
                nxt = (kt, p)
                break
        if prev is None or prev[1] is None:
            return None
        if nxt is None or nxt[1] is None:
            return prev[1]
        a = (t - prev[0]) / (nxt[0] - prev[0])
        return prev[1][0] + (nxt[1][0] - prev[1][0]) * a, prev[1][1] + (nxt[1][1] - prev[1][1]) * a


def demo_timeline(scene: Scene, scenarios: tuple[str, ...] = ("ok", "missing", "wrong_order"), gap: float = 2.0) -> Timeline:
    """三个周期：正常 / 漏贴标签 / 先贴标签（错序）。

    区域容错:通用工位(如向导部署)只有部分/零个 demo 区域时,缺区域的动作与置位直接跳过,
    不再 KeyError——sim 对任意 zones 配置都可运行(至少可演示人员类安全规则)。
    """
    tl = Timeline()
    geoms = scene.geoms
    c = scene.center
    t0 = 1.0
    for kind in scenarios:
        tl.set(t0, workpiece=True)
        t = t0
        if kind == "wrong_order" and "label_zone" in geoms:
            tl.hand(t + 1.6, c("label_zone")); tl.hand(t + 2.6, c("label_zone")); tl.hand(t + 2.8, None)
            tl.set(t + 2.2, label_zone=True)
            t += 3.0
        if "bin_harness" in geoms:
            tl.hand(t + 2.0, c("bin_harness")); tl.hand(t + 3.0, c("bin_harness"))
        if "connector_J1" in geoms:
            tl.hand(t + 3.8, c("connector_J1")); tl.hand(t + 4.6, c("connector_J1")); tl.hand(t + 4.8, None)
            tl.set(t + 4.0, connector_J1=True)
        if "B1" in geoms:
            tl.hand(t + 5.6, c("B1")); tl.hand(t + 6.8, c("B1")); tl.hand(t + 7.0, None)
            tl.set(t + 6.4, B1=True)
        if "B2" in geoms:
            tl.hand(t + 7.6, c("B2")); tl.hand(t + 8.6, c("B2")); tl.hand(t + 8.8, None)
            tl.set(t + 8.2, B2=True)
        if kind == "ok" and "label_zone" in geoms:
            tl.hand(t + 9.4, c("label_zone")); tl.hand(t + 10.2, c("label_zone")); tl.hand(t + 10.4, None)
            tl.set(t + 9.8, label_zone=True)
            t += 11.5
        else:
            t += 10.0
        tl.set(t, workpiece=False, connector_J1=False, B1=False, B2=False, label_zone=False)
        t0 = t + gap + 1.0
    tl.end = t0 + 1.0
    return tl


class SimSource:
    def __init__(self, cfg: StationConfig, timeline: Timeline | None = None):
        self.cfg = cfg
        self.scene = Scene(cfg)
        self.timeline = timeline or demo_timeline(self.scene, tuple(cfg.source.scenarios))
        self.fps = cfg.source.fps
        self.loop = cfg.source.loop
        self.idx = 0
        self._offset = 0.0  # 循环播放时让时间戳保持单调递增
        self._applied = 0
        self._changes = sorted(self.timeline.state_changes, key=lambda c: c[0])

    def read(self) -> tuple[float, np.ndarray] | None:
        local = self.idx / self.fps
        if local > self.timeline.end:
            if not self.loop:
                return None
            self._offset += local
            self.idx, self._applied = 0, 0
            for k in self.scene.on:
                self.scene.on[k] = False
            local = 0.0
        while self._applied < len(self._changes) and self._changes[self._applied][0] <= local:
            self.scene.on.update(self._changes[self._applied][1])
            self._applied += 1
        self.scene.hand = self.timeline.hand_at(local)
        frame = self.scene.render(self.idx)
        self.idx += 1
        return self._offset + local, frame

    def close(self) -> None:
        pass


class SimHands:
    """用仿真真值充当手部检测器（真实部署由 MediaPipeHands 替代）。"""

    def __init__(self, scene: Scene, cfg: StationConfig):
        self.scene = scene
        self.tracker = HandZoneTracker(scene.geoms, cfg.hands.enter_frames, cfg.hands.exit_frames, source="sim_hands")

    def process(self, frame: np.ndarray, ts: float, ctx: Context) -> list[Event]:
        h = self.scene.hand
        if h is None:
            return self.tracker.update([], ts)
        x, y = h
        ctx.hand_boxes.append((x - 28, y - 28, x + 28, y + 28))
        pts = [(x, y), (x + 10, y), (x - 10, y), (x, y + 10), (x, y - 10)]
        ctx.hand_points += pts
        return self.tracker.update(pts, ts)
