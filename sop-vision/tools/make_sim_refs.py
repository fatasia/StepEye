"""V5 冒烟数据:用仿真工位渲染已知真值的 OK/NG 参考图与待检图。

用法: python tools/make_sim_refs.py
输出: data/refs/first_article_demo/{ok,ng}/(服务端首件对照引用)
      .v0/simrefs/probe_*.jpg + truth.json(冒烟评测用,不入库)
真值由渲染状态直接决定,不存在标注歧义。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from visual.dsl import load_config  # noqa: E402
from visual.sim import Scene  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FULL = {"workpiece": True, "connector_J1": True, "B1": True, "B2": True, "label_zone": True}


def render_state(scene: Scene, on: dict[str, bool], idx: int) -> object:
    saved = dict(scene.on), scene.hand
    scene.on = {k: on.get(k, False) for k in scene.on}
    scene.hand = None
    frame = scene.render(idx, hand=False)
    scene.on, scene.hand = saved[0], saved[1]
    return frame


def main() -> int:
    scene = Scene(load_config(ROOT / "configs" / "demo_sim.yaml"))
    refs = ROOT / "data" / "refs" / "first_article_demo"
    probes = ROOT / ".v0" / "simrefs"
    for d in (refs / "ok", refs / "ng", probes):
        d.mkdir(parents=True, exist_ok=True)

    defects = {  # 每种缺陷 = 完整装配上去掉一个部件
        "ng_label": {**FULL, "label_zone": False},
        "ng_screw": {**FULL, "B2": False},
        "ng_connector": {**FULL, "connector_J1": False},
    }
    for i in range(3):  # OK 参考:同状态不同噪声种子
        cv2.imwrite(str(refs / "ok" / f"ok_{i + 1}.jpg"), render_state(scene, FULL, 10 * (i + 1)),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
    for i, (name, on) in enumerate(defects.items()):
        cv2.imwrite(str(refs / "ng" / f"{name}.jpg"), render_state(scene, on, 40 + 10 * i),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])

    truth = {}
    probe_plan = [("ok", FULL, 70), ("ok", FULL, 71), ("ng_label", defects["ng_label"], 72),
                  ("ok", FULL, 73), ("ng_screw", defects["ng_screw"], 74),
                  ("ng_connector", defects["ng_connector"], 75)]
    for i, (kind, on, idx) in enumerate(probe_plan, 1):
        name = f"probe_{i}.jpg"
        cv2.imwrite(str(probes / name), render_state(scene, on, idx), [cv2.IMWRITE_JPEG_QUALITY, 90])
        truth[name] = "PASS" if kind == "ok" else "NG"
    (probes / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK/NG 参考图 -> {refs}(3+3);待检 6 张 -> {probes};真值 {list(truth.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
