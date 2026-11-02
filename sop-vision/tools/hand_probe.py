"""真实视频上的手部检测统计（开发用）：检出率、手数、进出抖动、耗时；并导出标注抽帧。
用法：python tools/hand_probe.py 视频文件 输出目录 [缩放宽度 ...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # 直接 python tools/xxx.py 运行时，项目根不在 sys.path 里

from visual.dsl import HandsCfg, Zone  # noqa: E402
from visual.perception.base import Context, build_geoms  # noqa: E402
from visual.perception.hands import MediaPipeHands  # noqa: E402


def probe(video: str, out: Path, width: int, min_conf: float) -> dict:
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    ok, first = cap.read()
    h0, w0 = first.shape[:2]
    w = min(width, w0)
    h = int(h0 * w / w0)
    zones = [Zone(id="ALL", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)])]
    geoms = build_geoms(zones, w, h)
    hands = MediaPipeHands(HandsCfg(min_conf=min_conf), geoms, ROOT)
    ctx = Context(geoms)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    n = with_hand = 0
    counts: list[int] = []
    events = 0
    t_proc = 0.0
    samples: list[np.ndarray] = []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    pick = set(np.linspace(0, max(total - 1, 1), 8).astype(int))
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        fr = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
        ctx.reset()
        t = time.perf_counter()
        events += len(hands.process(fr, n / fps, ctx))
        t_proc += time.perf_counter() - t
        counts.append(len(ctx.hand_boxes))
        with_hand += bool(ctx.hand_boxes)
        if n in pick:
            vis = fr.copy()
            for b in ctx.hand_boxes:
                cv2.rectangle(vis, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (82, 172, 216), 2)
            for x, y in ctx.hand_points:
                cv2.circle(vis, (int(x), int(y)), 3, (193, 198, 62), -1)
            samples.append(cv2.resize(vis, (480, int(480 * h / w))))
        n += 1
    hands.close()
    if samples:
        rows = [np.hstack(samples[i:i + 4]) for i in range(0, len(samples) - len(samples) % 4, 4)]
        cv2.imwrite(str(out / f"{Path(video).stem}_{w}_c{min_conf}.jpg"), np.vstack(rows))
    return {"video": Path(video).name, "width": w, "conf": min_conf, "frames": n,
            "frames_with_hand_pct": round(100 * with_hand / max(n, 1), 1),
            "mean_hands": round(float(np.mean(counts)), 2), "max_hands": max(counts),
            "enter_exit_events": events, "ms_per_frame": round(1000 * t_proc / max(n, 1), 1)}


if __name__ == "__main__":
    video, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for width in [int(x) for x in sys.argv[3:]] or [640]:
        for conf in (0.5, 0.3):
            print(probe(video, out, width, conf), flush=True)
