"""备份/恢复核心:SQLite 一致性快照 + 关键文件清单复制,零新依赖。

设计约束:
- 备份绝不写入 data_dir;恢复绝不写入已存在目录(防原地覆盖事故),删除类操作一律不做。
- .db/.sqlite/.sqlite3 文件用 sqlite3 backup API 逐页快照,WAL 中未 checkpoint 的内容一并收进快照,
  因此 -wal/-shm/-journal 附属文件本身不复制。
- 非 db 文件按清单复制:顶层散落文件(如 ai-services.json)+ 默认目录清单(DEFAULT_FILE_DIRS,
  覆盖 labeling/{originals,images,versions}、documents、equipment-results 等落盘资产);
  其余顶层目录记入 manifest.skipped,不静默丢弃也不盲目拷贝(recordings 在外部目录,不在 data_dir)。
- manifest.json 记录每个文件的原始相对路径 + sha256;verify 只读校验;restore 前后各校验一次。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
JOURNAL_SUFFIXES = ("-wal", "-shm", "-journal")
# 落盘资产的默认清单:与 StationManager 建库位置对齐(server.py data_dir/*)。
DEFAULT_FILE_DIRS = ("labeling", "documents", "equipment-results", "action-references", "timelines", "evidence")
MANIFEST_NAME = "manifest.json"
_CHUNK = 1024 * 1024


class BackupError(Exception):
    """备份/恢复/校验失败;消息面向操作者,含可执行动作。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _is_db(name: str) -> bool:
    return name.lower().endswith(DB_SUFFIXES)


def _is_journal(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(suf) for suf in JOURNAL_SUFFIXES)


def _snapshot_db(src: Path, dst: Path, rel: str) -> None:
    """sqlite3 backup API:得到与源库事务一致的独立镜像,可在源库带 WAL 时安全执行。"""
    try:
        source = sqlite3.connect(str(src), timeout=10)
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
    except sqlite3.Error as e:
        if dst.exists():
            dst.unlink()
        raise BackupError(f"数据库 {rel} 快照失败({e});请确认文件是 SQLite 库且未被独占锁定") from None


def _classify(rel: Path, file_dirs: tuple[str, ...]) -> str:
    """返回 'db-snapshot' | 'file' | 'journal' | 'skip-dir' | 'skip-symlink'。"""
    name = rel.name
    if _is_journal(name):
        return "journal"
    if _is_db(name):
        return "db-snapshot"
    if len(rel.parts) == 1 or rel.parts[0] in file_dirs:
        return "file"
    return "skip-dir"


def backup(data_dir: Path, out_dir: Path, file_dirs: tuple[str, ...] = DEFAULT_FILE_DIRS) -> dict:
    """对 data_dir 做一致性备份,返回写入的 manifest;data_dir 只读,绝不改动。

    out_dir 不存在则创建;已存在且非空则拒绝(避免两次备份混在一个目录里)。
    """
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    if not data_dir.is_dir():
        raise BackupError(f"数据目录不存在:{data_dir}")
    if out_dir.resolve().is_relative_to(data_dir.resolve()):
        raise BackupError(f"备份输出目录 {out_dir} 不能在数据目录 {data_dir} 内部,否则备份会包含自身")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise BackupError(f"备份输出目录 {out_dir} 已存在且非空;请换一个新目录,避免混入旧备份")
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict] = []
    skipped: list[dict] = []
    for root, dirs, names in os.walk(data_dir, followlinks=False):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
        for name in sorted(names):
            path = root_path / name
            rel = path.relative_to(data_dir)
            kind = _classify(rel, file_dirs)
            if kind == "journal":
                skipped.append({"path": rel.as_posix(), "reason": "WAL/日志已并入对应数据库快照"})
                continue
            if kind == "skip-dir":
                skipped.append({"path": rel.as_posix(), "reason": "目录不在备份清单(file_dirs)"})
                continue
            if path.is_symlink():
                skipped.append({"path": rel.as_posix(), "reason": "符号链接不复制,防目录逃逸"})
                continue
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if kind == "db-snapshot":
                _snapshot_db(path, dest, rel.as_posix())
            else:
                shutil.copy2(path, dest)
            files.append({"path": rel.as_posix(), "sha256": _sha256(dest),
                          "bytes": dest.stat().st_size, "kind": kind})

    manifest = {"version": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": str(data_dir), "file_dirs": list(file_dirs),
                "files": files, "skipped": skipped}
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _load_manifest(backup_dir: Path) -> dict:
    path = backup_dir / MANIFEST_NAME
    if not path.is_file():
        raise BackupError(f"{backup_dir} 缺少 {MANIFEST_NAME},不是有效备份目录")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entries = manifest["files"]
        assert isinstance(entries, list)
    except (ValueError, KeyError, AssertionError):
        raise BackupError(f"{MANIFEST_NAME} 无法解析或缺少 files 清单") from None
    return manifest


def _check_tree(root: Path, manifest: dict) -> dict:
    """只读校验 root 下各清单文件的哈希;根目录下逐文件重算 sha256 并与 manifest 比对。"""
    missing, corrupt = [], []
    for entry in manifest["files"]:
        rel = str(entry["path"])
        pure = Path(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise BackupError(f"manifest 含非法路径条目 {rel!r},疑似被篡改,拒绝校验")
        path = root / pure
        if not path.is_file():
            missing.append(rel)
        elif _sha256(path) != entry.get("sha256"):
            corrupt.append(rel)
    return {"ok": not missing and not corrupt, "checked": len(manifest["files"]),
            "missing": missing, "corrupt": corrupt}


def verify(backup_dir: Path) -> dict:
    """只读校验备份目录:manifest 可解析、文件齐全、哈希一致。不改任何文件。"""
    return _check_tree(Path(backup_dir), _load_manifest(Path(backup_dir)))


def restore(backup_dir: Path, target_dir: Path) -> dict:
    """校验备份哈希后,恢复到全新目录;目标已存在则拒绝,恢复后逐文件复检。

    返回 {"restored": 文件数, "target": 目标路径}。
    """
    backup_dir, target_dir = Path(backup_dir), Path(target_dir)
    if target_dir.exists():
        raise BackupError(f"恢复目标 {target_dir} 已存在;只允许恢复到全新目录,防止原地覆盖事故")
    if target_dir.resolve().is_relative_to(backup_dir.resolve()):
        raise BackupError(f"恢复目标 {target_dir} 不能在备份目录 {backup_dir} 内部,否则会污染备份")
    manifest = _load_manifest(backup_dir)
    check = _check_tree(backup_dir, manifest)
    if not check["ok"]:
        raise BackupError(f"备份目录校验失败,先排查再恢复:缺失 {check['missing']},哈希不符 {check['corrupt']}")
    target_dir.mkdir(parents=True)
    for entry in manifest["files"]:
        src, dest = backup_dir / entry["path"], target_dir / entry["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    after = _check_tree(target_dir, manifest)
    if not after["ok"]:
        raise BackupError(f"恢复后复检失败(目标目录可能磁盘写坏):缺失 {after['missing']},哈希不符 {after['corrupt']}")
    return {"restored": len(manifest["files"]), "target": str(target_dir)}
