import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tools.training_preflight import inspect_dataset


def dataset(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "classes.txt").write_text("helmet\n", encoding="utf-8")
    licenses = [{"component": c, "license": "Apache-2.0", "source": "https://example.test/?token=DO_NOT_ECHO",
                 "checked_by": "test-fixture", "checked_at": "2026-11-03", "commercial_use": True}
                for c in ("dataset", "base_model")]
    (root / "metadata.json").write_text(json.dumps({"licenses": licenses}), encoding="utf-8")
    for split, shade in [("train", 40), ("val", 150)]:
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        ok, img = cv2.imencode(".png", np.full((32, 32, 3), shade, np.uint8)); assert ok
        (root / "images" / split / "a.png").write_bytes(img.tobytes())
        (root / "labels" / split / "a.txt").write_text("0 0.5 0.5 0.4 0.4\n", encoding="utf-8")
    return root


def test_structurally_ready_is_not_deployment_ready(tmp_path):
    root = dataset(tmp_path)
    report = inspect_dataset(root, min_images_per_class=2)
    assert report["status"] == "ready_for_training" and report["deployment_ready"] is False
    assert report["classes"][0]["total_unique_images"] == 2
    assert report["classes"][0]["train_boxes"] == 1
    assert "DO_NOT_ECHO" not in json.dumps(report)  # 许可来源里的查询凭据不进报告


def test_default_gate_blocks_tiny_dataset_and_missing_dataset(tmp_path):
    root = dataset(tmp_path)
    assert inspect_dataset(root)["blockers"]["insufficient_class_images"] == 1
    report = inspect_dataset(tmp_path / "missing")
    assert report["status"] == "blocked" and "missing_classes" in report["blockers"]


@pytest.mark.parametrize("text", ["0 .95 .5 .4 .4", "1 .5 .5 .2 .2", "0 nan .5 .2 .2", "0 .5 .5 0 .2", "0 .5 .5 .2"])
def test_invalid_or_out_of_bounds_label_is_blocked(tmp_path, text):
    root = dataset(tmp_path)
    (root / "labels/train/a.txt").write_text(text, encoding="utf-8")
    assert inspect_dataset(root, 1)["blockers"]["invalid_bbox"] == 1


def test_missing_annotation_orphan_and_unreviewed_empty_label(tmp_path):
    root = dataset(tmp_path)
    (root / "labels/train/a.txt").unlink()
    (root / "labels/train/orphan.txt").write_text("", encoding="utf-8")
    (root / "labels/val/a.txt").write_text("", encoding="utf-8")
    blockers = inspect_dataset(root, 1)["blockers"]
    assert blockers["missing_label"] == 1 and blockers["orphan_label"] == 1
    assert blockers["unreviewed_empty_label"] == 1


def test_exact_train_val_duplicate_cannot_inflate_class_size(tmp_path):
    root = dataset(tmp_path)
    (root / "images/val/a.png").write_bytes((root / "images/train/a.png").read_bytes())
    report = inspect_dataset(root, 2)
    assert report["sha_leak_groups"] == 1 and report["blockers"]["train_val_sha_leak"] == 1
    assert report["classes"][0]["total_unique_images"] == 1


def test_metadata_commercial_review_and_decode_are_required(tmp_path):
    root = dataset(tmp_path)
    (root / "metadata.json").write_text('{"licenses": []}', encoding="utf-8")
    (root / "images/train/a.png").write_bytes(b"not an image")
    blockers = inspect_dataset(root, 1)["blockers"]
    assert blockers["missing_license_dataset"] == 1 and blockers["missing_license_base_model"] == 1
    assert blockers["unreadable_image"] == 1


def test_invalid_policy_rejected(tmp_path):
    with pytest.raises(ValueError):
        inspect_dataset(tmp_path, 0)
