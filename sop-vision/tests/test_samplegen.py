"""样本生成与回归入库/边界卡的离线测试(不联网)。"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from visual.regress import RegressStore, boundary_card, manifest_hash
from visual.samplegen import augment_batch, augment_one, generate_batch, write_manifest
from visual.server import build_app


def _jpeg(tmp: Path, mean: int = 120, name: str = "ref.jpg") -> Path:
    rng = np.random.default_rng(0)
    img = np.clip(np.full((120, 160, 3), mean, np.float32) + rng.normal(0, 10, (120, 160, 3)), 0, 255).astype(np.uint8)
    p = tmp / name
    cv2.imwrite(str(p), img)
    return p


def test_augment_deterministic_and_label_inherited(tmp_path):
    ref = _jpeg(tmp_path)
    rows1 = augment_batch(ref, "NG", 5, tmp_path / "a", seed=42)
    rows2 = augment_batch(ref, "NG", 5, tmp_path / "b", seed=42)
    assert [r["params"] for r in rows1] == [r["params"] for r in rows2]  # 同种子同参数
    assert all(r["label"] == "NG" and r["engine"] == "augment" for r in rows1)
    for r in rows1:
        assert (tmp_path / "a" / r["file"]).is_file()
        assert "gamma" in r["params"]  # 光照扫掠必有
    imgs = [cv2.imread(str(tmp_path / "a" / r["file"])) for r in rows1]
    assert any(not np.array_equal(imgs[0], m) for m in imgs[1:])  # 变体之间确实不同


def test_augment_rejects_undecodable(tmp_path):
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"not-image")
    with pytest.raises(ValueError, match="无法解码"):
        augment_batch(bad, "OK", 3, tmp_path / "o")


def test_manifest_appends(tmp_path):
    ref = _jpeg(tmp_path)
    d = tmp_path / "out"
    write_manifest(d, augment_batch(ref, "OK", 2, d, seed=1))
    write_manifest(d, augment_batch(ref, "NG", 2, d, seed=1))
    lines = (d / "samples.manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4 and {json.loads(l)["label"] for l in lines} == {"OK", "NG"}


class _FakeResp:
    def __init__(self, data: bytes):
        self._d = data

    def read(self) -> bytes:
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeCogTransport:
    def post(self, url, headers, payload):
        assert payload["model"] == "cogview-3-flash" and "刮痕" in payload["prompt"]
        return {"data": [{"url": "https://fake/img.png"}]}


class _FakeVLM:
    def inspect(self, images, prompt):
        assert len(images) == 1
        return {"ok": True, "verdict": None, "confidence": None,
                "raw": '"金属件特写,出现刮痕"'}


def test_generate_batch_cogview_with_injections(tmp_path):
    ref = _jpeg(tmp_path)
    rows = generate_batch(ref, "NG", "表面出现刮痕", 3, tmp_path / "g", engine="cogview",
                          vlm=_FakeVLM(), cogview_transport=_FakeCogTransport(),
                          cogview_fetcher=lambda url, timeout: _FakeResp(b"fakedata"))
    assert len(rows) == 3 and all(r["engine"] == "cogview" and r["file"] for r in rows)
    assert all((tmp_path / "g" / r["file"]).read_bytes() == b"fakedata" for r in rows)
    assert all("保真度" in r["note"] or "冷启动" in r["note"] for r in rows)  # 诚实标注进 manifest


def test_generate_batch_cogview_caps_and_records_errors(tmp_path):
    ref = _jpeg(tmp_path)
    with pytest.raises(ValueError, match="最多"):
        generate_batch(ref, "NG", "d", 9, tmp_path / "g2", engine="cogview")
    def boom(url, timeout):
        raise RuntimeError("quota")
    rows = generate_batch(ref, "NG", "d", 2, tmp_path / "g3", engine="cogview", seed=1,
                          vlm=_FakeVLM(), cogview_transport=_FakeCogTransport(), cogview_fetcher=boom)
    assert all(r.get("error") for r in rows) and not any(r.get("file") for r in rows)  # 单张失败不断批


def test_regress_store_roundtrip(tmp_path):
    s = RegressStore(tmp_path / "regress.db")
    rec = {"name": "glove", "version": "v2", "agree_rate": 0.93, "created": "2026-11-03T10:00:00",
           "n": 14, "rows": []}
    rid = s.save(rec)
    assert s.get(rid)["version"] == "v2"
    assert [r["created"] for r in s.list("glove")] == ["2026-11-03T10:00:00"]
    assert s.get("nope") is None and s.list("other") == []
    s.close()


def test_boundary_card_contents(tmp_path):
    ref = _jpeg(tmp_path)
    rows = augment_batch(ref, "NG", 2, tmp_path / "c", seed=1)
    result = {"name": "glove", "version": "v3", "model": "glm-5.3-flash", "base_url": "https://x",
              "created": "2026-11-03T10:00:00", "n": 2, "agree": 2, "agree_rate": 1.0, "error_n": 0,
              "rows": [{**r, "pred": "PASS" if i == 0 else "NG", "expect": "NG", "latency_s": 2.0}
                       for i, r in enumerate(rows)]}
    card = boundary_card(result, "python -m visual regress m.yaml", "abc123")
    assert "能力边界卡" in card and "v3" in card and "abc123" in card
    assert "一致率:**1.0**" in card and "违规漏报" in card and "影子模式" in card
    assert "python -m visual regress m.yaml" in card


def test_manifest_hash_changes_with_image(tmp_path):
    ref = _jpeg(tmp_path)
    m = tmp_path / "m.yaml"
    m.write_text("name: t\nprompt: p\ncases:\n  - {image: ref.jpg, expect: NG}\n", encoding="utf-8")
    h1 = manifest_hash(m, tmp_path)
    _jpeg(tmp_path, mean=200)  # 同名覆盖 → 内容变
    assert manifest_hash(m, tmp_path) != h1


def test_samples_api_endpoint(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    ok, buf = cv2.imencode(".jpg", np.zeros((40, 60, 3), np.uint8))
    with TestClient(build_app([], data)) as client:
        r = client.post("/api/samples/generate",
                        files={"ref": ("ref.jpg", buf.tobytes(), "image/jpeg")},
                        data={"label": "OK", "n": "4", "engine": "augment"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["generated"] == 4
        assert all(row["label"] == "OK" for row in body["rows"])
        batch_dir = data / "samplegen" / "batch"
        assert (batch_dir / "samples.manifest.jsonl").is_file()
        bad = client.post("/api/samples/generate", files={"ref": ("ref.jpg", buf.tobytes(), "image/jpeg")},
                          data={"label": "OK", "n": "9", "engine": "cogview"})
        assert bad.status_code == 400  # 生成式上限拦截
