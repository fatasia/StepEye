"""文档中心:仓库 Markdown 只读服务;目录解析、穿越拒绝、资产白名单、标题提取。"""
import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI

from visual.project_docs import list_docs, read_asset, read_doc, router


@pytest.fixture
def api(tmp_path, monkeypatch):
    import visual.project_docs as pd
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "USAGE.md").write_text("# 使用指南\n\n正文 [链接](TECH-ARCHITECTURE.md)。\n", encoding="utf-8")
    (docs / "img.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (docs / "assets").mkdir()
    (docs / "assets" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (docs / "evil.exe").write_bytes(b"MZ")
    monkeypatch.setattr(pd, "doc_dirs", lambda: [docs])
    app = FastAPI()
    app.include_router(router())
    return TestClient(app)


def test_list_docs_extracts_title(api):
    items = api.get("/api/project-docs").json()
    assert [i["name"] for i in items] == ["USAGE.md"]
    assert items[0]["title"] == "使用指南" and items[0]["size"] > 0


def test_read_doc_and_asset(api):
    text = api.get("/api/project-docs/USAGE.md").text
    assert text.startswith("# 使用指南")
    assert api.get("/api/project-docs/USAGE.md").headers["content-type"].startswith("text/markdown")
    img = api.get("/api/project-docs/asset/img.png")
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    assert "image/png" in img.headers["content-type"]


def test_traversal_and_bad_names_rejected(api):
    for name in ("..\\secret.md", "../secret.md", "a/b.md", "no-suffix", "%2e%2e.md"):
        assert api.get(f"/api/project-docs/{name}").status_code in (404, 404)
    assert api.get("/api/project-docs/asset/img.exe").status_code == 404  # 扩展名白名单
    assert api.get("/api/project-docs/asset/assets/shot.png").status_code == 200  # 受控子目录
    assert api.get("/api/project-docs/asset/assets/%2e%2e/img.png").status_code in (404, 400)
    assert api.get("/api/project-docs/asset/missing.png").status_code == 404
    assert api.get("/api/project-docs/NOPE.md").status_code == 404


def test_reads_prefer_first_dir(tmp_path, monkeypatch):
    import visual.project_docs as pd
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "D.md").write_text("# from A", encoding="utf-8")
    (b / "D.md").write_text("# from B", encoding="utf-8")
    (b / "ONLY-B.md").write_text("# only b", encoding="utf-8")
    monkeypatch.setattr(pd, "doc_dirs", lambda: [a, b])
    assert read_doc("D.md") == "# from A"
    assert {i["name"] for i in list_docs()} == {"D.md", "ONLY-B.md"}
