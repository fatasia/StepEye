"""离线升级:版本比较、清单校验、安全解压与可回滚切换。"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from visual.upgrade import (ReleaseManifest, UpgradeError, apply_upgrade, is_newer,
                            parse_version, safe_extract_zip, swap_directories)


def test_parse_and_compare_versions():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.0-beta") == (1, 2, 0)
    assert is_newer("1.2.10", "1.2.9")
    assert is_newer("1.3", "1.2.99")
    assert not is_newer("1.2", "1.2.0")
    with pytest.raises(UpgradeError):
        parse_version("latest")


def _manifest(tmp_path: Path, archive: Path, version="1.1.0") -> ReleaseManifest:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return ReleaseManifest.from_mapping({
        "version": version, "archive": archive.name, "sha256": digest,
        "size": archive.stat().st_size, "notes": "test",
    }, tmp_path)


def _zip(path: Path, files: dict[str, str], prefix: str = "app") -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(f"{prefix}/{name}" if prefix else name, content)
    return path


def test_manifest_rejects_bad_digest_and_missing_archive(tmp_path):
    with pytest.raises(UpgradeError):
        ReleaseManifest.from_mapping({"version": "1.0.0", "archive": "a.zip", "sha256": "abc"})
    with pytest.raises(UpgradeError):
        ReleaseManifest.from_mapping({"version": "1.0.0", "sha256": "a" * 64})
    with pytest.raises(UpgradeError):
        ReleaseManifest.from_mapping({"version": "x", "archive": "a.zip", "sha256": "a" * 64})


def test_safe_extract_rejects_traversal_and_symlink(tmp_path):
    bad = _zip(tmp_path / "bad.zip", {"../evil.txt": "x"}, prefix="")
    with pytest.raises(UpgradeError, match="非法路径"):
        safe_extract_zip(bad, tmp_path / "out1")

    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as bundle:
        info = zipfile.ZipInfo("app/link")
        info.external_attr = (0o120777 << 16)
        bundle.writestr(info, "target")
    with pytest.raises(UpgradeError, match="符号链接"):
        safe_extract_zip(link, tmp_path / "out2")


def test_apply_upgrade_swaps_and_writes_version(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    (current / "version.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    (current / "old.txt").write_text("old", encoding="utf-8")
    archive = _zip(tmp_path / "pkg.zip", {"new.txt": "new", "version.json": '{"version":"1.1.0"}'})
    manifest = _manifest(tmp_path, archive)
    result = apply_upgrade(archive, manifest, current, tmp_path / "staging", tmp_path / "backup")
    assert result["from"] == "1.0.0" and result["to"] == "1.1.0"
    assert (current / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (current / "old.txt").exists()
    assert (tmp_path / "backup" / "old.txt").read_text(encoding="utf-8") == "old"
    assert json.loads((current / "version.json").read_text(encoding="utf-8"))["version"] == "1.1.0"


def test_apply_upgrade_rejects_same_or_older(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    (current / "version.json").write_text('{"version": "1.2.0"}', encoding="utf-8")
    archive = _zip(tmp_path / "pkg.zip", {"a.txt": "a"})
    manifest = _manifest(tmp_path, archive, version="1.2.0")
    with pytest.raises(UpgradeError, match="不高于当前"):
        apply_upgrade(archive, manifest, current, tmp_path / "staging", tmp_path / "backup")


def test_apply_upgrade_rejects_digest_mismatch(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    (current / "version.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    archive = _zip(tmp_path / "pkg.zip", {"a.txt": "a"})
    manifest = _manifest(tmp_path, archive)
    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(UpgradeError, match="大小不符|SHA256"):
        apply_upgrade(archive, manifest, current, tmp_path / "staging", tmp_path / "backup")


def test_swap_rolls_back_when_staged_missing(tmp_path):
    current = tmp_path / "current"
    current.mkdir()
    (current / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(UpgradeError, match="切换目录失败"):
        swap_directories(current, tmp_path / "missing", tmp_path / "backup")
    assert (current / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "backup").exists()
