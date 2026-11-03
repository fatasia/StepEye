"""存储治理:只读的用量统计与配额判定。

明确不做清理/删除(误删风险不可接受),只提供数据供告警与前端展示。
data_dir 实际结构见 StationManager:顶层是 sop.db/alerts.db 等散落库文件,
加 labeling/、evidence/、documents/、equipment-results/ 等子目录;
recordings 在外部 MONIBUCA_RECORD_DIR,不在此统计范围。
"""
from __future__ import annotations

import os
from pathlib import Path

# 顶层散落文件(库/配置)聚合计数的展示名,也可作为 limits 的键。
ROOT_FILES_BUCKET = "(根目录文件)"


def usage_report(data_dir: Path) -> dict:
    """按顶层条目统计字节与文件数,返回 {"items", "total_bytes", "total_files", "exists"}。

    - 每个顶层子目录一项;顶层散落文件聚合为 ROOT_FILES_BUCKET 一项(没有则为零项)。
    - 只读遍历,跳过符号链接,不跟随;目录不存在时返回 exists=False 的空报告而不抛错(新装环境)。
    """
    data_dir = Path(data_dir)
    items: list[dict] = []
    if not data_dir.is_dir():
        return {"items": items, "total_bytes": 0, "total_files": 0, "exists": False}

    def measure(path: Path) -> tuple[int, int]:
        bytes_total = files = 0
        for root, dirs, names in os.walk(path, followlinks=False):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
            for name in names:
                file = root_path / name
                if file.is_symlink():
                    continue
                try:
                    bytes_total += file.stat().st_size
                    files += 1
                except OSError:
                    continue  # 统计瞬间文件被并发删改:跳过,不让报表 500
        return bytes_total, files

    root_bytes = root_files = 0
    for entry in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            continue
        try:
            if entry.is_file():
                root_bytes += entry.stat().st_size
                root_files += 1
            elif entry.is_dir():
                nbytes, nfiles = measure(entry)
                items.append({"name": entry.name, "bytes": nbytes, "files": nfiles})
        except OSError:
            continue  # 条目在枚举与 stat 之间被并发删改:丢弃该条目,不中断报表
    if root_files:
        items.append({"name": ROOT_FILES_BUCKET, "bytes": root_bytes, "files": root_files})
    return {"items": items, "total_bytes": sum(i["bytes"] for i in items),
            "total_files": sum(i["files"] for i in items), "exists": True}


def quota_check(data_dir: Path, limits: dict[str, int]) -> list[dict]:
    """返回超出配额的条目列表 [{"name", "bytes", "limit"}],按超限倍率降序;纯读,绝不删文件。

    limits 键与 usage_report 的条目名对应(子目录名或 ROOT_FILES_BUCKET);
    limits 里写了不存在的目录按 0 字节计,不会误报超限。
    """
    if not limits:
        return []
    report = usage_report(data_dir)
    usage = {item["name"]: item["bytes"] for item in report["items"]}
    exceeded = []
    for name, limit in limits.items():
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError(f"配额必须是≥0的整数(字节):{name}={limit!r}")
        used = usage.get(name, 0)
        if used > limit:
            exceeded.append({"name": name, "bytes": used, "limit": limit})
    return sorted(exceeded, key=lambda e: -(e["bytes"] / e["limit"] if e["limit"] else float("inf")))
