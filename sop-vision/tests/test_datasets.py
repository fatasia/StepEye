"""V12 数据集导入测试:X-AnyLabeling 转换/YOLO 目录整理/类别校验/配对报告。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from visual.datasets import build_dataset, import_xanylabeling

CLASSES = ["helmet", "head"]


def _img(tmp: Path, name: str) -> None:
    cv2.imwrite(str(tmp / name), np.zeros((100, 200, 3), np.uint8))


def _xany(tmp: Path, name: str, shapes, w=200, h=100) -> None:
    import json
    (tmp / f"{name}.json").write_text(json.dumps({
        "imagePath": name, "imageWidth": w, "imageHeight": h, "shapes": shapes}), encoding="utf-8")


def test_import_xanylabeling_polygon_to_yolo(tmp_path):
    _img(tmp_path, "a.jpg")
    _xany(tmp_path, "a", [{"label": "helmet", "points": [[10, 10], [50, 10], [50, 40], [10, 40]]}])
    img_rel, lines = import_xanylabeling(tmp_path / "a.json", CLASSES)
    assert img_rel == "a" and len(lines) == 1  # 无扩展名 imagePath 由 build_dataset 的 stem 探测兜底
    cls, cx, cy, w, h = lines[0].split()
    assert cls == "0" and abs(float(w) - 0.2) < 1e-4 and abs(float(h) - 0.3) < 1e-4  # 40/200, 30/100


def test_import_unknown_class_rejected(tmp_path):
    _img(tmp_path, "b.jpg")
    _xany(tmp_path, "b", [{"label": "nope", "points": [[1, 1], [9, 9]]}])
    with pytest.raises(ValueError, match="不在 classes"):
        import_xanylabeling(tmp_path / "b.json", CLASSES)


def test_build_dataset_from_xanylabeling(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i, name in enumerate(("a", "b", "c", "d")):
        _img(src, f"{name}.jpg")
        _xany(src, name, [{"label": CLASSES[i % 2], "points": [[10, 10], [60, 10], [60, 60], [10, 60]]}])
    out = build_dataset(src, tmp_path / "out", CLASSES, val_ratio=0.25)
    assert out["train_n"] == 3 and out["val_n"] == 1
    assert (tmp_path / "out" / "dataset" / "classes.txt").read_text(encoding="utf-8") == "helmet\nhead"
    yaml_text = (tmp_path / "out" / "dataset" / "dataset.yaml").read_text(encoding="utf-8")
    assert "nc: 2" in yaml_text and "images/train" in yaml_text


def test_build_dataset_from_yolo_dir_and_empty_label_reported(tmp_path):
    src = tmp_path / "src"
    (src / "images" / "train").mkdir(parents=True)
    (src / "labels" / "train").mkdir(parents=True)
    _img(src / "images" / "train", "x.jpg")
    _img(src / "images" / "train", "y.jpg")
    (src / "labels" / "train" / "x.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    out = build_dataset(src, tmp_path / "out", CLASSES)
    assert out["train_n"] + out["val_n"] == 2
    assert any("无标注" in p for p in out["problems"])  # y.jpg 无标注被如实报告


def test_build_dataset_rejects_empty_source(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(ValueError, match="未找到"):
        build_dataset(src, tmp_path / "out", CLASSES)
