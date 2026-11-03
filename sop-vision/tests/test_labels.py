"""框级真值、并发修订、原图与分组数据版本的离线测试。"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import zipfile

from PIL import Image
import pytest
import yaml

from visual.labels import LabelError, LabelStore

TARGET = {"label_id": 0, "xyxy_norm": [.1, .2, .7, .8]}


def image_bytes(color=40, orientation=None) -> bytes:
    image = Image.new("RGB", (20, 10), (color, 50, 80))
    out = io.BytesIO()
    if orientation is not None:
        exif = Image.Exif(); exif[274] = orientation
        image.save(out, format="JPEG", exif=exif)
    else:
        image.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def store(tmp_path):
    s = LabelStore(tmp_path / "labels")
    yield s
    s.close()


def project(store):
    return store.create_project("安全帽", ["helmet", "head"], scene_id="ppe", license_ref="customer-approval-ref")


def confirmed(store, pid, color=40, metadata=None, background=False):
    asset = store.add_asset(pid, image_bytes(color), "same.png", metadata)
    if not background:
        asset = store.save_draft(asset["id"], [TARGET], asset["revision"], "inspector")
    return store.confirm(asset["id"], asset["revision"], "reviewer", background=background)


def test_project_asset_and_same_name_never_overwrite_originals(store):
    p = project(store)
    first = store.add_asset(p["id"], image_bytes(40), "C:\\capture\\same.png")
    second = store.add_asset(p["id"], image_bytes(80), "same.png")
    assert first["name"] == second["name"] == "same.png" and first["id"] != second["id"]
    assert first["sha256"] != second["sha256"]
    assert first["revision"] == 0 and first["state"] == "unlabeled" and first["targets"] == []
    assert set(f.read_bytes() for f in (store.root / "originals").iterdir()) == {image_bytes(40), image_bytes(80)}
    assert store.image_path(first["id"]) != store.image_path(second["id"])
    assert not any(k in first for k in ("original_file", "image_file", "image_path"))
    assert store.get_project(p["id"])["classes"] == ["helmet", "head"]
    assert store.list_projects()[0]["id"] == p["id"]


def test_exif_orientation_is_fixed_without_changing_raw(store):
    p = project(store)
    raw = image_bytes(70, orientation=6)
    a = store.add_asset(p["id"], raw, "phone.jpg")
    assert (a["width"], a["height"]) == (10, 20)
    with Image.open(store.image_path(a["id"])) as normalized:
        assert normalized.size == (10, 20) and normalized.getexif().get(274) is None
    assert next((store.root / "originals").iterdir()).read_bytes() == raw
    assert a["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("classes", [[], ["x", " x "], [""], [0], "person"])
def test_invalid_project_classes_are_rejected(store, classes):
    with pytest.raises(LabelError) as error:
        store.create_project("x", classes)
    assert error.value.status == 400


def test_invalid_images_and_limits_are_rejected(store, monkeypatch):
    p = project(store)
    for raw in (b"", b"not an image"):
        with pytest.raises(LabelError): store.add_asset(p["id"], raw, "x.png")
    monkeypatch.setattr("visual.labels.MAX_IMAGE_BYTES", 3)
    with pytest.raises(LabelError) as large:
        store.add_asset(p["id"], b"four", "x.png")
    assert large.value.status == 413
    monkeypatch.setattr("visual.labels.MAX_IMAGE_BYTES", 1024 * 1024)
    monkeypatch.setattr("visual.labels.MAX_IMAGE_PIXELS", 100)
    with pytest.raises(LabelError) as pixels:
        store.add_asset(p["id"], image_bytes(), "x.png")
    assert pixels.value.status == 413
    assert store.list_assets(p["id"])["total"] == 0


@pytest.mark.parametrize("target", [
    {"label_id": -1, "xyxy_norm": [0, 0, 1, 1]}, {"label_id": 2, "xyxy_norm": [0, 0, 1, 1]},
    {"label_id": True, "xyxy_norm": [0, 0, 1, 1]}, {"label_id": 0.0, "xyxy_norm": [0, 0, 1, 1]},
    {"label_id": 0, "xyxy_norm": [.5, .2, .1, .8]}, {"label_id": 0, "xyxy_norm": [0, 0, 0, 1]},
    {"label_id": 0, "xyxy_norm": [0, 0, 1.1, 1]}, {"label_id": 0, "xyxy_norm": [0, float("nan"), 1, 1]},
    {**TARGET, "confidence": float("inf")}, {**TARGET, "confidence": True},
])
def test_invalid_targets_never_create_revisions(store, target):
    p = project(store); a = store.add_asset(p["id"], image_bytes(), "x.png")
    with pytest.raises(LabelError) as error:
        store.save_draft(a["id"], [target], 0, "user")
    assert error.value.status == 400 and store.get_asset(a["id"])["revision"] == 0


def test_machine_empty_is_suggestion_not_background_and_confirmation_is_human(store):
    p = project(store); a = store.add_asset(p["id"], image_bytes(), "x.png")
    a = store.save_draft(a["id"], [], 0, "label-worker", producer="model", provenance={"job_id": "J1"})
    assert a["state"] == "suggested" and a["producer"] == "model" and a["provenance"]["job_id"] == "J1"
    with pytest.raises(LabelError): store.confirm(a["id"], 1, "reviewer")
    a = store.confirm(a["id"], 1, "reviewer", background=True)
    assert a["state"] == "background" and a["producer"] == "human" and a["background"]
    assert store.revisions(a["id"])[1]["state"] == "suggested"


def test_nonempty_annotations_cannot_be_confirmed_as_background(store):
    p = project(store); a = store.add_asset(p["id"], image_bytes(), "x.png")
    a = store.save_draft(a["id"], [TARGET], 0, "user")
    with pytest.raises(LabelError): store.confirm(a["id"], 1, "reviewer", background=True)
    with pytest.raises(LabelError): store.confirm(a["id"], 1, "", background=False)


def test_machine_cannot_replace_confirmed_truth_and_stale_revision_is_409(store):
    p = project(store); a = confirmed(store, p["id"])
    before = store.revisions(a["id"])
    for producer in ("model", "agent"):
        with pytest.raises(LabelError) as error:
            store.save_draft(a["id"], [], a["revision"], "worker", producer=producer)
        assert error.value.status == 409
    with pytest.raises(LabelError) as stale:
        store.save_draft(a["id"], [], 0, "person")
    assert stale.value.status == 409 and store.revisions(a["id"]) == before


def test_restore_creates_new_draft_and_old_revisions_remain_immutable(store):
    p = project(store); a = confirmed(store, p["id"])
    old = store.revisions(a["id"])
    restored = store.restore(a["id"], 0, a["revision"], "person")
    assert restored["revision"] == 3 and restored["state"] == "draft" and restored["targets"] == []
    assert restored["provenance"]["restore_revision"] == 0
    assert store.revisions(a["id"])[1:] == old
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("UPDATE label_revisions SET targets='[]' WHERE asset_id=?", (a["id"],))
    store._db.rollback()


def test_concurrent_stores_require_expected_revision_and_do_not_lose_edits(tmp_path):
    root = tmp_path / "labels"
    first, second = LabelStore(root), LabelStore(root)
    p = project(first); a = first.add_asset(p["id"], image_bytes(), "x.png")
    barrier = threading.Barrier(2)
    def save(s):
        barrier.wait()
        try: return s.save_draft(a["id"], [TARGET], 0, "person")["revision"]
        except LabelError as error: return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [first, second]))
    assert sorted(results) == [1, 409] and first.get_asset(a["id"])["revision"] == 1
    first.close(); second.close()


def test_reopen_and_additive_project_migration_preserve_records(tmp_path):
    root = tmp_path / "labels"; root.mkdir()
    db = sqlite3.connect(root / "labels.db")
    db.execute("CREATE TABLE label_projects(id TEXT PRIMARY KEY,name TEXT,classes TEXT,created_at TEXT)")
    db.execute("INSERT INTO label_projects VALUES ('old','旧项目','[\"person\"]','2026-01-01')")
    db.commit(); db.close()
    store = LabelStore(root)
    assert store.get_project("old")["scene_id"] == "" and store.get_project("old")["schema_revision"] == 1
    a = confirmed(store, "old")
    store.close()
    reopened = LabelStore(root)
    assert reopened.get_asset(a["id"])["state"] == "confirmed"
    assert len(reopened.revisions(a["id"])) == 3
    assert reopened._db.execute("PRAGMA user_version").fetchone()[0] == 1
    reopened.close()


def test_group_and_sha_transitively_stay_together_and_synthetic_never_enters_val(store):
    p = project(store); pid = p["id"]
    a = confirmed(store, pid, 10, {"group_id": "video-1"})
    b = confirmed(store, pid, 20, {"group_id": "video-1"})
    c = confirmed(store, pid, 20, {"group_id": "video-2"})
    d = confirmed(store, pid, 30, {"group_id": "video-2"})
    confirmed(store, pid, 50, {"group_id": "independent"})
    generated = confirmed(store, pid, 60, {"group_id": "generated", "synthetic": True})
    sibling = confirmed(store, pid, 70, {"group_id": "generated"})
    version = store.create_version(pid, seed=9, val_ratio=.5, actor="exporter")
    with zipfile.ZipFile(store.export_path(pid, version["id"])) as z:
        manifest = json.loads(z.read("manifest.json"))
    assignments = manifest["assignments"]
    assert len({assignments[x["id"]]["split"] for x in (a, b, c, d)}) == 1
    assert len({assignments[x["id"]]["group"] for x in (a, b, c, d)}) == 1
    assert assignments[generated["id"]]["split"] == assignments[sibling["id"]]["split"] == "train"
    assert version["train_count"] > 0 and version["val_count"] > 0


def test_version_snapshot_is_human_only_relative_and_immutable(store):
    p = project(store); pid = p["id"]
    box = confirmed(store, pid, 10)
    background = confirmed(store, pid, 20, background=True)
    pending = store.add_asset(pid, image_bytes(30), "pending.png")
    store.save_draft(pending["id"], [], 0, "worker", producer="model")
    version = store.create_version(pid, seed=1, actor="person")
    path = store.export_path(pid, version["id"])
    before = path.read_bytes()
    assert version["asset_count"] == 2 and version["excluded_unconfirmed"] == 1
    with zipfile.ZipFile(path) as z:
        manifest_bytes = z.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        assert hashlib.sha256(manifest_bytes).hexdigest() == version["manifest_sha256"]
        config = yaml.safe_load(z.read("dataset.yaml"))
        assert config["path"] == "." and not Path(config["path"]).is_absolute()
        labels = {name: z.read(name).decode() for name in z.namelist() if name.startswith("labels/")}
        assert len(labels) == 2 and "" in labels.values()
        nonempty = next(v for v in labels.values() if v)
        assert nonempty.split() == ["0", "0.40000000", "0.50000000", "0.60000000", "0.60000000"]
        metadata = json.loads(z.read("metadata.json"))
        assert len(metadata["reviewed_negatives"]) == 1 and metadata["licenses"] == []
        assert all(a["producer"] == "human" for a in manifest["assets"])
    assert store.create_version(pid, seed=1, actor="another")["id"] == version["id"]
    changed = store.save_draft(box["id"], [{"label_id": 1, "xyxy_norm": [0, 0, .5, .5]}], box["revision"], "person")
    store.confirm(box["id"], changed["revision"], "reviewer")
    assert store.create_version(pid, seed=1)["id"] != version["id"]
    assert path.read_bytes() == before and len(store.list_versions(pid)) == 2


def test_single_group_or_all_synthetic_exports_train_only_with_warning(store):
    p = project(store)
    confirmed(store, p["id"], metadata={"synthetic": True})
    version = store.create_version(p["id"])
    assert version["train_count"] == 1 and version["val_count"] == 0 and version["warnings"]
    assert store.quality(p["id"])["synthetic"] == 1


def test_quality_pagination_and_source_asset_group_inheritance(store):
    p = project(store); pid = p["id"]
    original = confirmed(store, pid, 10, {"group_id": "capture-1", "illumination_bucket": "night"})
    augmented = store.add_asset(pid, image_bytes(20), "a.png", {"source_asset_id": original["id"], "source_kind": "augment"})
    assert augmented["group_id"] == "capture-1" and augmented["synthetic"]
    out = store.quality(pid)
    assert out["states"]["confirmed"] == out["states"]["unlabeled"] == 1
    assert out["confirmed_total"] == 1 and out["class_counts"][0]["images"] == 1
    assert out["illumination_buckets"]["night"] == 1 and out["ready_for_export"]
    assert store.list_assets(pid, state="unlabeled")["items"][0]["id"] == augmented["id"]
    assert store.list_assets(pid, limit=1, offset=1)["total"] == 2
    other = project(store)
    with pytest.raises(LabelError):
        store.add_asset(other["id"], image_bytes(30), "cross.png", {"source_asset_id": original["id"]})


def test_version_integrity_and_cross_project_scope(store):
    p = project(store); confirmed(store, p["id"])
    version = store.create_version(p["id"])
    other = project(store)
    with pytest.raises(LabelError) as wrong:
        store.export_path(other["id"], version["id"])
    assert wrong.value.status == 404
    archive = store.export_path(p["id"], version["id"])
    archive.write_bytes(b"modified")
    with pytest.raises(LabelError) as changed:
        store.export_path(p["id"], version["id"])
    assert changed.value.status == 409


@pytest.mark.parametrize("seed,ratio", [(True, .2), (42, float("nan")), (42, 1), (42, -.1)])
def test_version_split_options_are_validated(store, seed, ratio):
    p = project(store)
    with pytest.raises(LabelError): store.create_version(p["id"], seed=seed, val_ratio=ratio)


def test_no_confirmed_assets_cannot_export_and_future_schema_refuses_to_open(store, tmp_path):
    p = project(store)
    store.add_asset(p["id"], image_bytes(), "x.png")
    with pytest.raises(LabelError) as empty:
        store.create_version(p["id"])
    assert empty.value.status == 409
    root = tmp_path / "future"; root.mkdir()
    db = sqlite3.connect(root / "labels.db"); db.execute("PRAGMA user_version=99"); db.close()
    with pytest.raises(LabelError) as future: LabelStore(root)
    assert future.value.status == 409


def test_concurrent_exports_commit_one_complete_version(tmp_path):
    root = tmp_path / "labels"
    first, second = LabelStore(root), LabelStore(root)
    p = project(first); confirmed(first, p["id"], 10); confirmed(first, p["id"], 30)
    barrier = threading.Barrier(2)
    def export(s):
        barrier.wait()
        return s.create_version(p["id"], actor="person")
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(export, [first, second]))
    assert versions[0]["id"] == versions[1]["id"] and len(first.list_versions(p["id"])) == 1
    assert first.export_path(p["id"], versions[0]["id"]).is_file()
    assert not list((root / "versions").glob(".tmp-*"))
    first.close(); second.close()


def test_complete_orphan_version_directory_is_adopted_after_restart(tmp_path):
    root = tmp_path / "labels"; store = LabelStore(root)
    p = project(store); confirmed(store, p["id"])
    version = store.create_version(p["id"])
    before = store.export_path(p["id"], version["id"]).read_bytes()
    store.close()
    # 模拟 rename 已成功但目录记录事务尚未提交；真实 API 没有删除版本能力。
    db = sqlite3.connect(root / "labels.db")
    db.execute("DROP TRIGGER label_version_no_delete")
    db.execute("DELETE FROM label_versions")
    db.commit(); db.close()
    reopened = LabelStore(root)
    restored = reopened.create_version(p["id"])
    assert restored["id"] == version["id"] and reopened.export_path(p["id"], restored["id"]).read_bytes() == before
    reopened.close()
