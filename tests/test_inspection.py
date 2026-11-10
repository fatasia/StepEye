"""拍照即检纵切片离线测试:假传输、临时库与目录,不联网、不依赖真实凭据。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from visual.inspection import InspectionStore, load_scenarios
from visual.llm_provider import ProviderConfig, Transport
from visual.server import build_app

VERDICT_JSON = '{"verdict": "NG", "confidence": 0.9, "reason": "双手裸露"}'


class CannedTransport(Transport):
    """始终返回同一 JSON 判定,记录调用次数。"""

    def __init__(self, content: str = VERDICT_JSON):
        self.content = content
        self.calls = 0

    def post(self, url, headers, payload):
        self.calls += 1
        prompt = payload['messages'][0]['content'][-1]['text']
        if '"differences"' in prompt:
            return {"choices": [{"message": {"content": '{"differences":[]}'}}]}
        return {"choices": [{"message": {"content": self.content}}]}


def jpeg_bytes(w: int = 96, h: int = 64) -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 128, np.uint8))
    assert ok
    return buf.tobytes()


def write_yaml(path: Path, ok_ref: str, ng_ref: str) -> None:
    path.write_text(
        "scenarios:\n"
        "  - id: glove_check\n"
        "    name: 手套佩戴检查\n"
        "    enabled: true\n"
        "    mode: single\n"
        "    prompt: '判断是否戴手套,只输出JSON'\n"
        "  - id: first_article\n"
        "    name: 首件对照\n"
        "    enabled: true\n"
        "    mode: first_article\n"
        f"    ok_refs: [{ok_ref}]\n"
        f"    ng_refs: [{ng_ref}]\n"
        "  - id: off_scenario\n"
        "    name: 未启用场景\n"
        "    enabled: false\n"
        "    prompt: 'x'\n",
        encoding="utf-8",
    )


@pytest.fixture()
def env(tmp_path):
    """带 lifespan 的客户端 + 罐头传输 + 数据目录。"""
    data = tmp_path / "data"
    (data / "refs" / "fa" / "ok").mkdir(parents=True)
    (data / "refs" / "fa" / "ng").mkdir(parents=True)
    for i in range(2):
        cv2.imwrite(str(data / "refs" / "fa" / "ok" / f"ok{i}.jpg"), np.zeros((32, 32, 3), np.uint8))
        cv2.imwrite(str(data / "refs" / "fa" / "ng" / f"ng{i}.jpg"), np.zeros((32, 32, 3), np.uint8))
    cfg = tmp_path / "inspection.yaml"
    write_yaml(cfg, "refs/fa/ok", "refs/fa/ng")
    transport = CannedTransport()
    app = build_app([], data, inspection_config=cfg, vlm_transport=transport,
                    provider_cfg=ProviderConfig(api_key="local-test-transport"))
    # 必须进入上下文:lifespan 里才会启动判定线程
    with TestClient(app) as client:
        yield client, transport, data


def _wait_done(client, tid: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rec = client.get(f"/api/inspection/tasks/{tid}").json()
        if rec["status"] in ("done", "failed"):
            return rec
        time.sleep(0.05)
    raise AssertionError("任务未在预期时间内完成")


def test_scenarios_listed_with_enabled_flag(env):
    client, _, _ = env
    rows = client.get("/api/inspection/scenarios").json()
    assert {r["id"]: r["enabled"] for r in rows} == {"glove_check": True, "first_article": True, "off_scenario": False}


def test_submit_single_task_lifecycle(env):
    client, transport, data = env
    r = client.post("/api/inspection/tasks", data={"scenario_id": "glove_check"},
                    files=[("files", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 202 and r.json()["status"] == "pending"
    rec = _wait_done(client, r.json()["id"])
    assert rec["status"] == "done" and rec["verdict"] == "NG" and rec["confidence"] == 0.9
    # 图片落盘且可回读
    img_url = rec["image_urls"][0]
    assert client.get(img_url).status_code == 200
    assert transport.calls == 1


def test_first_article_uses_two_step(env):
    client, transport, _ = env
    r = client.post("/api/inspection/tasks", data={"scenario_id": "first_article"},
                    files=[("files", ("p.jpg", jpeg_bytes(), "image/jpeg"))])
    rec = _wait_done(client, r.json()["id"])
    assert rec["status"] == "done"
    assert transport.calls == 2  # ①列差异 ②判定


def test_multi_image_takes_worst_verdict(env):
    client, transport, _ = env
    # 3 张图 3 次调用:前两次 PASS 高置信,最后一次 NG —— 最坏结果应为 NG
    transport.script_verdicts = None  # noqa: 属性仅为文档性说明;CannedTransport 恒 NG,多图必 NG
    r = client.post("/api/inspection/tasks", data={"scenario_id": "glove_check"},
                    files=[("files", (f"{i}.jpg", jpeg_bytes(), "image/jpeg")) for i in range(3)])
    rec = _wait_done(client, r.json()["id"])
    assert rec["verdict"] == "NG" and transport.calls == 3


def test_disabled_scenario_rejected_with_reason(env):
    client, _, _ = env
    r = client.post("/api/inspection/tasks", data={"scenario_id": "off_scenario"},
                    files=[("files", ("a.jpg", jpeg_bytes(), "image/jpeg"))])
    assert r.status_code == 409 and "未启用" in r.json()["detail"]


def test_unknown_scenario_and_bad_image(env):
    client, _, _ = env
    assert client.post("/api/inspection/tasks", data={"scenario_id": "nope"},
                       files=[("files", ("a.jpg", jpeg_bytes(), "image/jpeg"))]).status_code == 404
    r = client.post("/api/inspection/tasks", data={"scenario_id": "glove_check"},
                    files=[("files", ("a.txt", b"not-an-image", "text/plain"))])
    assert r.status_code == 400 and "可解码" in r.json()["detail"]


def test_inspection_disabled_without_key(tmp_path):
    from visual.llm_provider import ProviderConfig
    data = tmp_path / "d"
    data.mkdir()
    cfg = tmp_path / "inspection.yaml"
    write_yaml(cfg, "x", "y")
    # 注入空凭据,绕开本机 .env 里可能存在的真实 key,确定性覆盖「未配置」分支
    app = build_app([], data, inspection_config=cfg, provider_cfg=ProviderConfig(api_key=""))
    with TestClient(app) as client:
        r = client.get("/api/inspection/scenarios")
        assert r.status_code == 503 and "ZHIPU_API_KEY" in r.json()["detail"]


def test_store_list_filter_and_stats(tmp_path):
    store = InspectionStore(tmp_path / "insp.db")
    for i, (status, verdict) in enumerate([("done", "NG"), ("done", "PASS"), ("failed", None)]):
        store.save({"id": f"T{i}", "scenario": "s", "status": status, "verdict": verdict,
                    "confidence": 0.9, "reason": "", "images": [], "created": f"2026-11-02T10:0{i}"})
    assert [r["id"] for r in store.list(status="done")] == ["T1", "T0"]  # created 倒序
    stats = store.stats()
    assert stats["total"] == 3 and stats["by_verdict"] == {"NG": 1, "PASS": 1}
    assert store.get("T0")["status"] == "done" and store.get("missing") is None


def test_load_scenarios_rejects_bad_config(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("scenarios:\n  - id: a\n  - id: a\n  - {id: b, prompt: p}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="id 重复"):
        load_scenarios(bad)
    fa = tmp_path / "fa.yaml"
    fa.write_text("scenarios:\n  - {id: a, prompt: p, mode: first_article}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ok_refs"):
        load_scenarios(fa)

def test_inspection_worker_starts_on_demand_and_retires(env):
    client, transport, _ = env
    service = client.app.state.manager.inspection
    assert service._thread is None
    for _ in range(2):
        job = client.post("/api/inspection/tasks", data={"scenario_id":"glove_check"},
                          files=[("files", ("frame.jpg", jpeg_bytes(), "image/jpeg"))]).json()
        assert _wait_done(client, job["id"])["status"] == "done"
        end = time.monotonic() + 2
        while service._thread is not None and time.monotonic() < end:
            time.sleep(.01)
        assert service._thread is None
    assert transport.calls == 2


def test_inspection_shutdown_finishes_job_before_closing_store(tmp_path):
    import threading
    from visual.inspection import InspectionService, Scenario

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    store = InspectionStore(tmp_path / "inspection.sqlite")
    original_close = store.close
    def close():
        original_close()
        closed.set()
    store.close = close
    service = InspectionService([Scenario("test", "test", prompt="test", enabled=True)],
                                store, tmp_path / "images", None)
    def judge(*args):
        entered.set()
        assert release.wait(5)
        return "PASS", 1., "ok"
    service._judge = judge
    service.start()
    job = service.submit("test", [jpeg_bytes()])
    assert entered.wait(2)
    worker = service._thread
    stopping = threading.Thread(target=lambda: service.stop(close_store=True))
    stopping.start()
    try:
        assert not closed.wait(.05)
        assert store.get(job["id"])["status"] == "running"
    finally:
        release.set()
        stopping.join(5)
        worker.join(5)
    assert closed.is_set()
    assert service._thread is None
    reopened = InspectionStore(tmp_path / "inspection.sqlite")
    try:
        assert reopened.get(job["id"])["status"] == "done"
    finally:
        reopened.close()
