"""V0 续:从测试视频抽帧,生成带索引的迷你评测集与拼图(人工看图标注用)。

用法: python tools/make_eval_set.py <视频1> [视频2 ...] <输出目录> [每视频帧数]
输出: <目录>/eval_NN.jpg(单帧) + <目录>/sheet_M.jpg(4格拼图,带帧号) + manifest.json
拼图只用于人工标注;判定评测跑在单帧上。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np


def spread_frames(video: Path, n: int) -> list:
    """在视频时长上均匀取 n 帧,跳过开头结尾各 5%,避开黑场。"""
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    if total <= 0:
        raise SystemExit(f"读不到帧数: {video}")
    picks = [int(total * (0.05 + 0.9 * (i + 0.5) / n)) for i in range(n)]
    out = []
    for fidx in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > 1024:
            frame = cv2.resize(frame, (1024, int(h * 1024 / w)))
        out.append((video.stem, fidx, fidx / fps, frame))
    cap.release()
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) < 2:
        print("用法: python tools/make_eval_set.py <视频...> <输出目录> [每视频帧数]")
        return 2
    # 尾部数字参数 = 每个视频的抽帧数:传 1 个应用到全部,传 N 个与视频一一对应
    tail = [int(a) for a in args if a.isdigit()]
    if tail:
        args = [a for a in args if not a.isdigit()]
    *videos, out_dir = args
    if len(tail) == 1:
        counts = [tail[0]] * len(videos)
    elif len(tail) == len(videos):
        counts = tail
    else:
        raise SystemExit(f"帧数参数({tail})与视频数({len(videos)})不匹配")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    idx = 0
    frames = []
    for v, n in zip(videos, counts):
        for stem, fidx, sec, frame in spread_frames(Path(v), n):
            name = f"eval_{idx:02d}.jpg"
            cv2.imwrite(str(out / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            manifest.append({"file": name, "src": stem, "frame": fidx, "sec": round(sec, 1)})
            frames.append((name, frame))
            idx += 1
    # 4 格拼图,标注帧号,供人工快速过一遍
    for s in range(0, len(frames), 4):
        batch = frames[s:s + 4]
        tiles = []
        for name, f in batch:
            t = f.copy()
            cv2.rectangle(t, (0, 0), (240, 34), (0, 0, 0), -1)
            cv2.putText(t, name, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            tiles.append(t)
        while len(tiles) < 4:
            tiles.append(np.zeros_like(tiles[0]))
        top = np.hstack(tiles[:2])
        bot = np.hstack(tiles[2:4])
        sheet = np.vstack([top, bot])
        cv2.imwrite(str(out / f"sheet_{s // 4}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"共 {len(manifest)} 帧 -> {out}(拼图 {(len(frames) + 3) // 4} 张,人工标注用)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
