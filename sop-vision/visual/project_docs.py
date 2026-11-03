"""项目文档中心:只读提供仓库 Markdown 文档与配图,路径白名单防穿越。

来源目录按优先级:打包内 docs/ → 源码仓库 docs/(开发环境才有);同名文件取前者。
文档清单含首个标题作展示名;图片等配图按扩展名白名单放行,其余一律 404。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

ROOT = Path(__file__).resolve().parents[1]
DOC_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.md$")
ASSET_NAME = re.compile(r"^(?:assets/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}

TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def doc_dirs() -> list[Path]:
    out = []
    for candidate in (ROOT / "docs", ROOT.parent / "docs"):
        if candidate.is_dir() and candidate not in out:
            out.append(candidate)
    return out


def _resolve(name: str, exts: frozenset[str] | None) -> Path:
    if exts == frozenset({".md"}):
        if not DOC_NAME.match(name):
            raise HTTPException(404, "文档不存在")
    else:
        # 资产最多允许受控的 assets/ 一级子目录;正则排除 .. 与多余分隔,无穿越面
        if not ASSET_NAME.match(name) or Path(name).suffix.lower() not in (exts or frozenset()):
            raise HTTPException(404, "文档不存在")
    for directory in doc_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise HTTPException(404, "文档不存在")


def list_docs() -> list[dict]:
    """全部文档按名称排序;同名取高优先目录;标题取首个一级标题,缺省用文件名。"""
    seen: dict[str, Path] = {}
    for directory in doc_dirs():
        for path in sorted(directory.glob("*.md")):
            seen.setdefault(path.name, path)
    items = []
    for name, path in sorted(seen.items()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            continue
        match = TITLE.search(text)
        items.append({"name": name, "title": match.group(1).strip() if match else path.stem,
                      "size": path.stat().st_size})
    return items


def read_doc(name: str) -> str:
    path = _resolve(name, frozenset({".md"}))
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(404, "文档不存在") from exc


def read_asset(name: str) -> FileResponse:
    path = _resolve(name, frozenset(ASSET_EXTS))
    media = "image/svg+xml" if path.suffix.lower() == ".svg" else f"image/{path.suffix.lower().lstrip('.')}"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "max-age=300"})


def router() -> APIRouter:
    api = APIRouter()

    @api.get("/api/project-docs")
    def listing(request: Request) -> list[dict]:
        return list_docs()

    @api.get("/api/project-docs/{name}")
    def doc(name: str) -> Response:
        return Response(read_doc(name), media_type="text/markdown; charset=utf-8")

    @api.get("/api/project-docs/asset/{name:path}")
    def asset(name: str) -> FileResponse:
        return read_asset(name)

    return api
