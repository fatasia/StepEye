from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .dsl import Program, load_config


def _validate(args: argparse.Namespace) -> int:
    for path in args.configs:
        cfg = load_config(path)
        prog = Program.from_config(cfg)
        print(f"{cfg.station}  规则指纹 {prog.rules_hash}  节拍 {cfg.cycle.takt_s}s")
        for s in prog.steps:
            pre = ",".join(s.eff_preds) or "-"
            print(f"  {s.id:<10} {s.name:<12} 前驱[{pre}]  {'可选 ' if s.optional else ''}"
                  f"{s.evidence.mode}: {'; '.join(c.describe() for c in s.evidence.conds)}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from .runtime import StationRuntime

    cfg = load_config(args.config)
    cfg.source.realtime = False
    if args.video:
        cfg.source.type, cfg.source.uri = "file", args.video
    data_dir = Path(args.data or tempfile.mkdtemp(prefix="visual-"))
    rt = StationRuntime(cfg, data_dir, base_dir=Path(__file__).resolve().parents[1])
    rt.listeners.append(lambda k, p: print(json.dumps(p, ensure_ascii=False)) if k == "cycle_end" else None)
    try:
        rt.run()
    finally:
        rt.close()
    print(f"# 证据与记录目录: {data_dir}", file=sys.stderr)
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    import cv2

    from .perception.base import build_geoms
    from .perception.roi_state import RoiStatePerceiver
    from .sources import CvSource

    cfg = load_config(args.config)
    if cfg.source.type == "sim":
        print("仿真工位使用内置参考图，无需标定", file=sys.stderr)
        return 1
    zones = args.zones.split(",") if args.zones else None
    src = CvSource(cfg.source.type, cfg.source.uri, cfg.source.fps, False, False)
    frame = None
    for _ in range(10):  # 丢弃前几帧，等待曝光稳定
        item = src.read()
        frame = item[1] if item else frame
    src.close()
    if frame is None:
        print("取不到画面，请检查视频源", file=sys.stderr)
        return 1
    frame = cv2.resize(frame, (cfg.source.width, cfg.source.height))
    roi = RoiStatePerceiver(cfg.roi_states, build_geoms(cfg.zones, cfg.source.width, cfg.source.height),
                            Path(args.data) / "refs" / cfg.station)
    print("已标定:", ", ".join(roi.calibrate(frame, zones)))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import build_app

    app = build_app([Path(c) for c in args.configs], Path(args.data))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if not stream.isatty():  # 管道输出（日志 / JSON 行）统一 UTF-8，避免中文乱码
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="visual", description="纯视觉 SOP 合规监测")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="校验并展示工位规则")
    v.add_argument("configs", nargs="+")
    v.set_defaults(fn=_validate)

    r = sub.add_parser("run", help="离线回放：跑完视频源并逐周期输出 JSON")
    r.add_argument("config")
    r.add_argument("--video", help="覆盖配置里的视频源，使用本地视频文件")
    r.add_argument("--data", help="输出目录（默认临时目录）")
    r.set_defaults(fn=_run)

    c = sub.add_parser("calibrate", help="采集结果核验的参考图（先把区域摆到参考状态）")
    c.add_argument("config")
    c.add_argument("--zones", help="逗号分隔的区域 id；缺省为全部")
    c.add_argument("--data", default="data")
    c.set_defaults(fn=_calibrate)

    s = sub.add_parser("serve", help="启动服务与 Web 界面")
    s.add_argument("configs", nargs="+")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data", default="data")
    s.set_defaults(fn=_serve)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
