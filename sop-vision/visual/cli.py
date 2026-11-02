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


def _regress_cmd(args: argparse.Namespace) -> int:
    from .regress import _regress
    return _regress(args)


def _gen_samples(args: argparse.Namespace) -> int:
    from .samplegen import COGVIEW_MAX_BATCH, generate_batch, write_manifest

    if args.engine == "cogview" and args.n > COGVIEW_MAX_BATCH:
        print(f"生成式一次最多 {COGVIEW_MAX_BATCH} 张(保真度有限,别当验收样本)", file=sys.stderr)
        return 2
    rows = generate_batch(Path(args.ref), args.label.upper(), args.desc, args.n, Path(args.out),
                          engine=args.engine, seed=args.seed)
    ok_n = sum(1 for r in rows if r.get("file"))
    mp = write_manifest(Path(args.out), rows)
    print(f"生成 {ok_n}/{len(rows)} 张 -> {Path(args.out)}  manifest -> {mp}")
    return 0 if ok_n else 1


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import build_app

    inspection = Path(args.inspection) if args.inspection else None
    app = build_app([Path(c) for c in args.configs], Path(args.data), inspection_config=inspection)
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
    s.add_argument("--inspection", help="拍照即检场景配置(如 configs/inspection.yaml);不传则禁用该功能")
    s.set_defaults(fn=_serve)

    g = sub.add_parser("regress", help="评测回归:固定样本集跑分,--against 对比上一份结果防退化")
    g.add_argument("manifest", help="回归清单 YAML(name/prompt/cases)")
    g.add_argument("--data", help="样本目录(缺省为清单所在目录)")
    g.add_argument("--out", help="结果 JSON 输出路径(缺省 <data>/regress_result.json)")
    g.add_argument("--against", help="上一份结果 JSON,输出回归/改进对比")
    g.add_argument("--store", action="store_true", help="结果入库 <data>/regress.db(版本化)")
    g.add_argument("--card", help="产出能力边界卡 markdown 到指定路径")
    g.set_defaults(fn=_regress_cmd)

    s2 = sub.add_parser("gen-samples", help="样本生成:参考图+描述 → 批量带标注样本")
    s2.add_argument("--ref", required=True, help="参考图路径")
    s2.add_argument("--label", required=True, help="样本标签(OK/NG/...),标注直接继承")
    s2.add_argument("--desc", default="与参考图同类场景", help="样本描述(生成式引擎用)")
    s2.add_argument("--n", type=int, default=10, help="生成数量(augment 默认10;cogview 上限8)")
    s2.add_argument("--engine", choices=["augment", "cogview"], default="augment",
                    help="augment=离线变换(秒级);cogview=生成式(VLM 写提示词→CogView)")
    s2.add_argument("--out", required=True, help="输出目录(manifest 追加写入)")
    s2.add_argument("--seed", type=int, default=7)
    s2.set_defaults(fn=_gen_samples)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
