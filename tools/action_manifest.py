"""从人工时间轴导出动作片段索引；不将单图框或模型建议当作动作标签。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual.timeline import TimelineStore, check_intervals


def build(data_dir: Path, groups: dict) -> dict:
    """groups按时间轴ID明确登记场景、采集组和划分；未登记的录像不读。"""
    if not groups or not (data_dir / "timelines.db").is_file():
        raise ValueError("需要现有时间轴数据库和非空的分组登记")
    store = TimelineStore(data_dir / "timelines.db")
    rows, assigns = [], {}
    try:
        for tid, meta in sorted(groups.items()):
            group = meta.get("group")
            split = meta.get("split")
            if not isinstance(group, str) or not group.strip() or split not in ("train", "val", "test"):
                raise ValueError(f"{tid}: 需要采集组和明确的train/val/test划分")
            rec = store.get(tid)
            if not rec or rec["status"] != "annotated" or not rec["intervals"]:
                raise ValueError(f"{tid}: 缺少已标注的动作区间")
            check = check_intervals(rec["intervals"], rec["duration_s"])
            if not check["ok"]:
                raise ValueError(f"{tid}: {check['problems']}")
            root = (data_dir / "timelines").resolve()
            path = (root / rec["video"]).resolve()
            if path.parent != root or not path.is_file():
                raise ValueError(f"{tid}: 原录像不存在")
            with path.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            for k in (("group", group), ("source_sha256", sha)):
                if assigns.setdefault(k, split) != split:
                    raise ValueError(f"{tid}: 同一采集组或录像跨越了训练/验证/测试集")
            for iv in rec["intervals"]:
                rows.append({"timeline_id": tid, "video_sha256": sha, "source_group": group, "split": split,
                             "action": iv["step"], "start_s": float(iv["start"]), "end_s": float(iv["end"])})
    finally:
        store.close()
    return {"schema_version": 1, "clips": rows, "purpose": "annotation_index"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("groups", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    result = build(args.data_dir, json.loads(args.groups.read_text(encoding="utf-8")))
    with args.out.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
