"""WHEP 同源代理契约测试；不代替真实 Monibuca/ICE 验收。"""
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from visual.auth import AuthStore
from visual.dsl import SourceCfg
from visual.media import MediaBridge, MediaError
from visual.server import build_app


@pytest.mark.parametrize("path", ["../x", "a/../b", "http://host/x", "a?key=x", "a//b", "/a", "a%2fb"])
def test_preview_stream_rejects_url_and_traversal(path):
    with pytest.raises(ValidationError):
        SourceCfg(preview_stream=path)


def test_bridge_offer_and_owner_cleanup():
    calls = []
    def handle(req):
        calls.append((req.method, req.url.path, req.url.query.decode()))
        if req.method == "POST":
            assert req.headers["content-type"] == "application/sdp"
            return httpx.Response(201, content=b"v=0\r\nanswer", headers={"Location": "/webrtc/session/abc"})
        return httpx.Response(204)
    bridge = MediaBridge("http://media:8180", transport=httpx.MockTransport(handle))
    answer, token = bridge.offer("live/test", b"v=0\r\noffer", "ST", "viewer")
    assert answer.startswith(b"v=0") and token
    with pytest.raises(MediaError) as e:
        bridge.release(token, "ST", "other")
    assert e.value.status == 403
    bridge.release(token, "ST", "viewer")
    bridge.release(token, "ST", "viewer")  # 幂等
    assert calls == [("POST", "/webrtc/whep", "app=live&stream=test"),
                     ("DELETE", "/webrtc/session/abc", "")]


def test_bridge_v5_keeps_path_style():
    calls = []
    def handle(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(201, content=b"v=0\r\nanswer", headers={"Location": "/webrtc/session/1"})
    bridge = MediaBridge("http://media", prefix="/webrtc/play", transport=httpx.MockTransport(handle))
    bridge.offer("live/test", b"v=0", "ST", "u")
    assert calls == [("POST", "/webrtc/play/live/test")]


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, text="not SDP"),
    httpx.Response(201, text="v=0", headers={"Location": "http://other/webrtc/session/1"}),
    httpx.Response(201, text="v=0", headers={"Location": "http://media/webrtc/%2e%2e/api/config"})])
def test_bridge_rejects_upstream_errors_and_foreign_location(response):
    b = MediaBridge("http://media", transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(MediaError):
        b.offer("live/test", b"v=0", "ST", "u")


def test_preview_auth_and_config(tmp_path):
    cfg = Path(__file__).resolve().parents[1] / "configs/demo_sim.yaml"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    raw["source"]["preview_stream"] = "live/test"
    p = tmp_path / "st.yaml"; p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    auth = AuthStore(tmp_path / "auth.db")
    auth.set_user("reader", "password123", "viewer")
    app = build_app([p], tmp_path / "data", auth=auth)
    app.state.media = MediaBridge("http://media", transport=httpx.MockTransport(
        lambda r: httpx.Response(201, content=b"v=0\r\nanswer", headers={"Location": "/webrtc/session/1"})
        if r.method == "POST" else httpx.Response(204)))
    with TestClient(app) as c:
        path = "/api/stations/DEMO-ST01/preview"
        assert c.post(path, content=b"v=0").status_code == 401
        assert c.post("/api/auth/login", json={"username": "reader", "password": "password123"}).status_code == 200
        assert c.get("/api/stations/DEMO-ST01/config").json()["preview"]["available"]
        assert c.post(path, content=b"v=0").status_code == 415
        headers = {"Content-Type": "application/sdp"}
        assert c.post(path, content=b"bad", headers=headers).status_code == 400
        assert c.post(path, content=b"v=0" + b"x" * 131072, headers=headers).status_code == 413
        res = c.post(path, content=b"v=0\r\noffer", headers=headers)
        assert res.status_code == 201 and res.headers["location"].startswith(path + "/")
        assert c.delete(res.headers["location"]).status_code == 204
        assert c.post("/api/stations/DEMO-ST01/zones", json={"zones": []}).status_code == 403
