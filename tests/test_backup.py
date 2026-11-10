"""备份/恢复闭环:备份→篡改→verify 报错→restore 拒绝已存在目标→恢复哈希一致→WAL 快照可打开。"""
import hashlib
import json
import sqlite3

import pytest

from visual.backup import BackupError, backup, restore, verify


@pytest.fixture
def data_dir(tmp_path):
    """贴近 StationManager 实际布局:散落库 + labeling/documents 子目录 + 清单外目录。"""
    root = tmp_path / "data"
    (root / "labeling" / "labels").mkdir(parents=True)
    (root / "labeling" / "versions").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    (root / "other").mkdir(parents=True)

    plain = sqlite3.connect(root / "sop.db")
    plain.executescript("CREATE TABLE t(a); INSERT INTO t VALUES (7);")
    plain.commit()
    plain.close()

    (root / "labeling" / "labels" / "img1.png").write_bytes(b"\x89PNG-fake-1")
    (root / "labeling" / "versions" / "v1.bin").write_bytes(b"version-payload")
    (root / "documents" / "doc.txt").write_bytes(b"hello-doc")
    (root / "ai-services.json").write_bytes(b"{}")
    (root / "other" / "stuff.bin").write_bytes(b"not-in-manifest")
    return root


@pytest.fixture
def wal_db(data_dir):
    """WAL 库:关闭自动 checkpoint 且连接保持打开,确保 -wal 里有未并入主库的内容。"""
    conn = sqlite3.connect(data_dir / "alerts.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, msg TEXT)")
    conn.execute("INSERT INTO alerts(msg) VALUES ('pending-alert')")
    conn.commit()
    assert (data_dir / "alerts.db-wal").exists()
    return conn


def test_backup_manifest_and_skip_list(data_dir, tmp_path, wal_db):
    out = tmp_path / "bk"
    manifest = backup(data_dir, out)
    paths = {f["path"]: f["kind"] for f in manifest["files"]}
    assert paths["sop.db"] == "db-snapshot"
    assert paths["alerts.db"] == "db-snapshot"
    assert paths["labeling/labels/img1.png"] == "file"
    assert paths["labeling/versions/v1.bin"] == "file"
    assert paths["documents/doc.txt"] == "file"
    assert paths["ai-services.json"] == "file"
    skipped = {s["path"] for s in manifest["skipped"]}
    assert "other/stuff.bin" in skipped          # 清单外目录:记录而非静默
    assert "alerts.db-wal" in skipped            # 日志文件:已并入快照
    assert not (out / "alerts.db-wal").exists()
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])
    assert verify(out)["ok"]


def test_restore_to_fresh_dir_wal_readable(data_dir, tmp_path, wal_db):
    out = tmp_path / "bk"
    backup(data_dir, out)
    target = tmp_path / "restored"
    result = restore(out, target)
    assert result["restored"] == len(json.loads((out / "manifest.json").read_text(encoding="utf-8"))["files"])

    # 恢复产物与 manifest 逐文件哈希一致
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        digest = hashlib.sha256((target / entry["path"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["path"]

    # WAL 库快照可打开,未 checkpoint 的内容不丢
    restored = sqlite3.connect(target / "alerts.db")
    try:
        assert restored.execute("SELECT msg FROM alerts").fetchall() == [("pending-alert",)]
    finally:
        restored.close()
    plain = sqlite3.connect(target / "sop.db")
    try:
        assert plain.execute("SELECT a FROM t").fetchall() == [(7,)]
    finally:
        plain.close()
    wal_db.close()


def test_verify_detects_tamper_and_missing(data_dir, tmp_path):
    out = tmp_path / "bk"
    backup(data_dir, out)
    (out / "labeling" / "labels" / "img1.png").write_bytes(b"tampered")
    (out / "documents" / "doc.txt").unlink()
    check = verify(out)
    assert check["ok"] is False
    assert check["corrupt"] == ["labeling/labels/img1.png"]
    assert check["missing"] == ["documents/doc.txt"]


def test_restore_refuses_existing_target(data_dir, tmp_path):
    out = tmp_path / "bk"
    backup(data_dir, out)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_bytes(b"do-not-lose-me")
    with pytest.raises(BackupError, match="已存在"):
        restore(out, occupied)
    assert (occupied / "keep.txt").read_bytes() == b"do-not-lose-me"  # 原地覆盖事故被拦截
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BackupError, match="已存在"):
        restore(out, empty)
    assert list(empty.iterdir()) == []


def test_restore_refuses_corrupt_backup_and_leaves_no_target(data_dir, tmp_path):
    out = tmp_path / "bk"
    backup(data_dir, out)
    (out / "ai-services.json").write_bytes(b'{"evil":1}')
    target = tmp_path / "never-created"
    with pytest.raises(BackupError, match="校验失败"):
        restore(out, target)
    assert not target.exists()


def test_backup_refuses_dirty_out_dir(data_dir, tmp_path):
    out = tmp_path / "dirty"
    out.mkdir()
    (out / "old.txt").write_bytes(b"old")
    with pytest.raises(BackupError, match="非空"):
        backup(data_dir, out)


def test_manifest_illegal_path_rejected(tmp_path):
    """被篡改的 manifest 带 .. 逃逸路径时,校验必须拒绝而不是读出目录外文件。"""
    bk = tmp_path / "bk"
    bk.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"s")
    (bk / "manifest.json").write_text(json.dumps(
        {"version": 1, "files": [{"path": "../secret.txt", "sha256": "0" * 64, "bytes": 1, "kind": "file"}]}),
        encoding="utf-8")
    with pytest.raises(BackupError, match="非法路径"):
        verify(bk)
