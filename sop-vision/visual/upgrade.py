"""离线升级包:版本比较、摘要校验、安全解压与可回滚目录切换。

设计约束:
- 升级包是 zip;拒绝绝对路径、``..``、符号链接与超量解压(默认 8GB)。
- 切换目录采用 "current → backup, staged → current" 两次 rename;
  第二步失败时把 backup 改回 current,不留下半装状态。
- 不做网络下载、不读注册表、不注册系统服务;由部署脚本负责停进程与启动。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_UNCOMPRESSED_BYTES = 8 * 1024**3
VERSION_FILE = "version.json"


class UpgradeError(Exception):
    """升级包或切换过程不可继续。"""


def parse_version(text: str) -> tuple[int, ...]:
    """解析 1 / 1.2 / v1.2.3 为整数元组;预发布后缀(1.2.0-beta)按主版本比较。"""
    raw = str(text or "").strip()
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    core = raw.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if not parts or any(not p.isdigit() for p in parts):
        raise UpgradeError(f"版本号无效：{text!r}")
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, current: str) -> bool:
    """候选版本是否严格高于当前版本(缺失段按 0 补齐)。"""
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    archive: str
    sha256: str
    size: int | None = None
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict, base_dir: Path | None = None) -> "ReleaseManifest":
        if not isinstance(data, dict):
            raise UpgradeError("升级清单必须是 JSON 对象")
        version = str(data.get("version") or "").strip()
        archive = str(data.get("archive") or "").strip()
        digest = str(data.get("sha256") or "").strip().lower()
        parse_version(version)
        if not archive:
            raise UpgradeError("升级清单缺少 archive")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise UpgradeError("升级清单 sha256 必须是 64 位十六进制")
        size = data.get("size")
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise UpgradeError("升级清单 size 必须是非负整数")
        path = Path(archive)
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        return cls(version=version, archive=str(path), sha256=digest, size=size,
                   notes=str(data.get("notes") or ""),
                   extra={k: v for k, v in data.items()
                          if k not in {"version", "archive", "sha256", "size", "notes"}})

    @classmethod
    def load(cls, path: Path) -> "ReleaseManifest":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpgradeError(f"升级清单不可读：{exc}") from exc
        return cls.from_mapping(data, Path(path).resolve().parent)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        raise UpgradeError(f"升级包不可读：{exc}") from exc
    return digest.hexdigest()


def verify_archive(path: Path, manifest: ReleaseManifest) -> None:
    archive = Path(path)
    if not archive.is_file():
        raise UpgradeError("升级包不存在")
    if manifest.size is not None and archive.stat().st_size != manifest.size:
        raise UpgradeError(f"升级包大小不符：{archive.stat().st_size} != {manifest.size}")
    actual = sha256_file(archive)
    if actual != manifest.sha256:
        raise UpgradeError("升级包 SHA256 校验失败")


def _safe_member(name: str) -> PurePosixPath:
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise UpgradeError(f"升级包包含非法路径：{name}")
    if pure.parts and pure.parts[0].endswith(":"):
        raise UpgradeError(f"升级包包含盘符路径：{name}")
    return pure


def safe_extract_zip(archive: Path, dest: Path, max_bytes: int = MAX_UNCOMPRESSED_BYTES) -> Path:
    """解压到空目录;拒绝路径穿越、符号链接与超量解压。返回解压根目录。"""
    target = Path(dest)
    if target.exists() and any(target.iterdir()):
        raise UpgradeError("解压目标必须为空目录")
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                pure = _safe_member(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise UpgradeError(f"升级包包含符号链接：{info.filename}")
                total += info.file_size
                if total > max_bytes:
                    raise UpgradeError("升级包解压后超过大小上限")
                out = target.joinpath(*pure.parts)
                if info.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpgradeError(f"升级包解压失败：{exc}") from exc
    return target


def read_current_version(current_dir: Path) -> str:
    try:
        data = json.loads((Path(current_dir) / VERSION_FILE).read_text(encoding="utf-8"))
        return str(data.get("version") or "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def _single_root(extracted: Path) -> Path:
    children = list(extracted.iterdir())
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted


def swap_directories(current: Path, staged: Path, backup: Path) -> None:
    """current→backup、staged→current;任一步失败都尽量恢复原目录。"""
    current, staged, backup = Path(current), Path(staged), Path(backup)
    if backup.exists():
        raise UpgradeError("备份目录已存在，请先人工确认或清理")
    moved_current = False
    try:
        if current.exists():
            os.replace(current, backup)
            moved_current = True
        os.replace(staged, current)
    except OSError as exc:
        if moved_current and not current.exists() and backup.exists():
            try:
                os.replace(backup, current)
            except OSError:
                pass
        raise UpgradeError(f"切换目录失败：{exc}") from exc


def apply_upgrade(archive: Path, manifest: ReleaseManifest, current_dir: Path,
                  staging_dir: Path, backup_dir: Path, dry_run: bool = False) -> dict:
    """校验并切换;dry_run 只做校验与解压前检查,不写目录。"""
    current_dir, staging_dir, backup_dir = Path(current_dir), Path(staging_dir), Path(backup_dir)
    current_version = read_current_version(current_dir)
    if not is_newer(manifest.version, current_version):
        raise UpgradeError(f"候选版本 {manifest.version} 不高于当前 {current_version}")
    verify_archive(archive, manifest)
    if dry_run:
        return {"ok": True, "dry_run": True, "from": current_version,
                "to": manifest.version, "archive": str(archive)}
    if staging_dir.exists() and any(staging_dir.iterdir()):
        raise UpgradeError("暂存目录必须为空")
    extracted = safe_extract_zip(archive, staging_dir)
    app_root = _single_root(extracted)
    swap_directories(current_dir, app_root, backup_dir)
    (current_dir / VERSION_FILE).write_text(
        json.dumps({"version": manifest.version, "notes": manifest.notes,
                    "manifest": manifest.extra}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"ok": True, "from": current_version, "to": manifest.version,
            "backup": str(backup_dir), "current": str(current_dir)}
