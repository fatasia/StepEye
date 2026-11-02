"""上线向导(V10)测试:项目 CRUD、文档编译(假传输)、步骤写回、部署热加岗。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from visual.server import build_app
from visual.wizard import WizardStore, apply_steps, compile_steps, skeleton_yaml

STEPS_JSON = json.dumps({"steps": [
    {"name": "取料", "evidence": "hand", "hint": "手进料盒"},
    {"name": "确认装配", "evidence": "vlm", "hint": "视觉判定"}]}, ensure_ascii=False)


class _FakeTransport:
    def post(self, url, headers, payload):
        return {"choices": [{"message": {"content": STEPS_JSON}}]}


def test_wizard_store_and_skeleton(tmp_path):
    s = WizardStore(tmp_path / "w.db")
    rec = s.create("东门岗", skeleton_yaml("WZ-1", "东门岗"))
    assert rec["status"] == "draft" and "东门岗" in rec["yaml_text"] and "person_absent" in rec["yaml_text"]
    assert s.list()[0]["id"] == rec["id"]
    s.add_asset(rec["id"], {"kind": "sample", "file": "a.jpg", "label": "OK"})
    assert s.get(rec["id"])["assets"][0]["label"] == "OK"
    s.update(rec["id"], status="verified")
    assert s.get(rec["id"])["status"] == "verified"
    s.close()


def test_compile_steps_parses_and_fails_gracefully():
    out = compile_steps.__wrapped__ if hasattr(compile_steps, "__wrapped__") else compile_steps
    from visual.llm_provider import ProviderConfig, VLMProvider
    vlm = VLMProvider(ProviderConfig(api_key="k", max_retries=0), _FakeTransport())
    out = compile_steps(vlm, "1. 取料 2. 装配")
    assert out["ok"] and out["steps"][0]["id"] == "S1" and out["steps"][0]["evidence"] == "hand"
    class _Bad:
        def post(self, *a):
            return {"choices": [{"message": {"content": "我觉得是三步"}}]}
    out2 = compile_steps(VLMProvider(ProviderConfig(api_key="k", max_retries=0), _Bad()), "文本")
    assert not out2["ok"] and "编译失败" in out2["error"]  # 解析失败按业务失败,原文留复核


def test_apply_steps_writes_valid_yaml():
    y = apply_steps(skeleton_yaml("WZ-1", "t"),
                    [{"name": "取料", "evidence": "hand"}, {"name": "确认", "evidence": "vlm"}])
    import yaml
    raw = yaml.safe_load(y)
    assert [s["id"] for s in raw["steps"]] == ["S1", "S2"]
    assert raw["steps"][1]["after"] == ["S1"]  # 顺序链
    assert "vlm_check" in json.dumps(raw["steps"][1]["evidence"], ensure_ascii=False)
    assert raw["cycle"]["start"]["person_present"]["zone"] == "work_area"


def test_wizard_api_end_to_end_deploy(tmp_path):
    app = build_app([], tmp_path / "data", provider_cfg=None)
    with TestClient(app) as client:
        r = client.post("/api/wizard/projects", json={"name": "东门岗"})
        assert r.status_code == 200 and r.json()["status"] == "draft"
        pid = r.json()["id"]
        # 未确认步骤就部署 → 409
        assert client.post(f"/api/wizard/projects/{pid}/deploy").status_code == 409
        # 编译(无凭据 → 503 明示)
        r = client.post(f"/api/wizard/projects/{pid}/compile", json={"text": "1.取料"})
        assert r.status_code == 503
        # 直接确认步骤(人确认路径,不依赖 LLM)
        r = client.post(f"/api/wizard/projects/{pid}/steps",
                        json={"steps": [{"name": "取料", "evidence": "hand"}]})
        assert r.status_code == 200 and r.json()["status"] == "verified"
        # 占位 RTSP 直接部署 → fail-fast 400(不产生坏源)
        r = client.post(f"/api/wizard/projects/{pid}/deploy")
        assert r.status_code == 400 and "无效的 RTSP" in r.json()["detail"]
        # 填源(演示用 sim)→ 部署 → 热加岗 → health 多一个工位
        assert client.post(f"/api/wizard/projects/{pid}/source", json={"type": "sim", "uri": ""}).status_code == 200
        r = client.post(f"/api/wizard/projects/{pid}/deploy")
        assert r.status_code == 200, r.text
        assert client.get("/api/health").json()["stations"] == 1
        assert client.get("/api/stations").json()[0]["meta"]["id"].startswith("WZ-")
        # 重复部署 → 409(id 已存在)
        assert client.post(f"/api/wizard/projects/{pid}/deploy").status_code == 409
        assert client.get(f"/api/wizard/projects/{pid}").json()["status"] == "live"
