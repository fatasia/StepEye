"""检测数据集训练前检查；不训练、不导入 torch，不等于模型发布验收。

python tools/training_preflight.py datasets/ppe_v1 --out data/ppe-preflight.json
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sys

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def inspect_dataset(dataset: Path, min_images_per_class: int = 500, min_val_ratio: float = 0.15) -> dict:
    if min_images_per_class < 1 or not 0 < min_val_ratio < 1:
        raise ValueError("样本阈值须为正数，验证集比例须在 0..1 之间")
    root = dataset.resolve()
    blockers, examples = Counter(), []
    def issue(code, path=None, line=None):
        blockers[code] += 1
        if len(examples) < 50:
            examples.append({"code": code, **({"path": path} if path else {}), **({"line": line} if line else {})})
    def safe(path):
        return not path.is_symlink() and path.resolve().is_relative_to(root)
    def files(folder, extensions):
        if not folder.is_dir() or not safe(folder):
            issue("missing_or_unsafe_directory", folder.relative_to(root).as_posix()); return []
        return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in extensions)
    classes_path = root / "classes.txt"
    classes = []
    if classes_path.is_file() and safe(classes_path):
        try:
            classes = classes_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            issue("unreadable_classes")
    if not classes:
        issue("missing_classes")
    if any(not c.strip() for c in classes) or len(set(classes)) != len(classes):
        issue("blank_or_duplicate_classes")
    metadata = {}
    metadata_path = root / "metadata.json"
    try:
        if not safe(metadata_path):
            raise ValueError("unsafe metadata")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be object")
        licenses = metadata.get("licenses", [])
        if not isinstance(licenses, list):
            raise ValueError("licenses must be list")
        for component in ("dataset", "base_model"):
            entries = [e for e in licenses if isinstance(e, dict) and e.get("component") == component]
            if not entries:
                issue("missing_license_" + component)
            for entry in entries:
                valid = all(isinstance(entry.get(k), str) and entry[k].strip()
                            for k in ("license", "source", "checked_by", "checked_at"))
                try:
                    valid = valid and date.fromisoformat(entry["checked_at"]).isoformat() == entry["checked_at"]
                except (ValueError, KeyError, TypeError):
                    valid = False
                if not valid or entry.get("commercial_use") is not True:
                    issue("license_review_incomplete_" + component)
    except (OSError, UnicodeError, ValueError, TypeError):
        issue("missing_or_invalid_metadata")
        metadata = {}
    reviewed_negatives = metadata.get("reviewed_negatives", [])
    if not isinstance(reviewed_negatives, list):
        issue("invalid_reviewed_negatives"); reviewed_negatives = []
    images, instances = {}, {s: Counter() for s in ("train", "val")}
    class_hashes = {s: defaultdict(set) for s in ("train", "val")}
    hashes, image_counts, negatives = defaultdict(list), {}, Counter()
    for split in ("train", "val"):
        images[split] = files(root / "images" / split, IMAGE_EXTS)
        labels = files(root / "labels" / split, {".txt"})
        image_counts[split] = len(images[split])
        if not images[split]: issue("empty_images_" + split)
        paired, stems = set(), set()
        for image in images[split]:
            rel = image.relative_to(root).as_posix()
            stem = image.relative_to(root / "images" / split).with_suffix("")
            label = root / "labels" / split / stem.with_suffix(".txt")
            if stem in stems: issue("ambiguous_image_stem", rel)
            stems.add(stem); paired.add(label)
            if not safe(image) or not safe(label):
                issue("unsafe_path", rel); continue
            try:
                if image.stat().st_size > 64 * 1024 * 1024: raise ValueError("image too large")
                blob = image.read_bytes()
                decoded = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
                if decoded is None: raise ValueError("invalid image")
                digest = hashlib.sha256(blob).hexdigest()
                hashes[digest].append((split, rel))
            except (OSError, ValueError, cv2.error):
                issue("unreadable_image", rel); continue
            if not label.is_file():
                issue("missing_label", rel); continue
            try:
                lines = label.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeError):
                issue("unreadable_label", label.relative_to(root).as_posix()); continue
            nonempty, seen_classes = [x for x in lines if x.strip()], set()
            if not nonempty:
                negatives[split] += 1
                if label.relative_to(root).as_posix() not in reviewed_negatives:
                    issue("unreviewed_empty_label", label.relative_to(root).as_posix())
            for line_no, line in enumerate(lines, 1):
                if not line.strip(): continue
                try:
                    fields = line.split()
                    if len(fields) != 5: raise ValueError("expected bbox")
                    cid = int(fields[0]); x, y, w, h = map(float, fields[1:])
                    if not 0 <= cid < len(classes): raise ValueError("unknown class")
                    if not all(math.isfinite(v) for v in (x, y, w, h)): raise ValueError("nonfinite")
                    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1): raise ValueError("range")
                    if min(x-w/2, y-h/2) < -1e-6 or max(x+w/2, y+h/2) > 1+1e-6: raise ValueError("bounds")
                    instances[split][cid] += 1; seen_classes.add(cid)
                except (ValueError, OverflowError):
                    issue("invalid_bbox", label.relative_to(root).as_posix(), line_no)
            for cid in seen_classes: class_hashes[split][cid].add(digest)
        for label in labels:
            if label not in paired: issue("orphan_label", label.relative_to(root).as_posix())
    leaks = [items for items in hashes.values() if len({s for s, _ in items}) > 1]
    for items in leaks: issue("train_val_sha_leak", items[0][1])
    stats = []
    for cid, name in enumerate(classes):
        train, val = class_hashes["train"][cid], class_hashes["val"][cid]
        total, ratio = len(train | val), len(val) / max(1, len(train | val))
        if total < min_images_per_class: issue("insufficient_class_images", f"class:{cid}")
        if not train or not val or ratio < min_val_ratio: issue("insufficient_class_split", f"class:{cid}")
        stats.append({"id": cid, "name": name, "train_unique_images": len(train), "val_unique_images": len(val),
                      "total_unique_images": total, "val_ratio": round(ratio, 4),
                      "train_boxes": instances["train"][cid], "val_boxes": instances["val"][cid]})
    return {"status": "blocked" if blockers else "ready_for_training", "deployment_ready": False,
            "dataset": str(root), "policy": {"min_images_per_class": min_images_per_class, "min_val_ratio": min_val_ratio},
            "images": image_counts, "reviewed_or_unreviewed_empty_labels": dict(negatives), "classes": stats,
            "sha_leak_groups": len(leaks), "duplicate_groups": sum(len(v) > 1 for v in hashes.values()),
            "blockers": dict(blockers), "examples": examples,
            "limitations": ["许可只检查台账字段，不代替权利审核", "未验证标注准确性、光照/场景覆盖和近似重复帧",
                            "训练后仍需检测器回归与真实影子验证，不能据此发布模型"]}


def main() -> int:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", type=Path); p.add_argument("--out", type=Path)
    p.add_argument("--min-images-per-class", type=int, default=500)
    p.add_argument("--min-val-ratio", type=float, default=0.15)
    args = p.parse_args()
    report = inspect_dataset(args.dataset, args.min_images_per_class, args.min_val_ratio)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True); args.out.write_text(text, encoding="utf-8")
    print(text)
    return 2 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
