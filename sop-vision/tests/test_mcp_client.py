"""MCP proxy bounds and credential isolation, independent of the optional SDK."""
import asyncio
import json
import socket

import httpx
import pytest

from visual.mcp_server import ApiError, McpConfig, McpConfigError, StepEyeApi, _endpoint, _identifier, _page

TOKEN = "se_mcp_" + "fixtureonly_not_a_real_credential"


def config(**kwargs):
    return McpConfig("http://127.0.0.1:8000", TOKEN, **kwargs)


@pytest.mark.parametrize("url", ["http://8.8.8.8", "http://169.254.169.254", "http://0.0.0.0", "http://[fe80::1]",
    "file:///etc/passwd", "https://u:p@127.0.0.1", "http://127.0.0.1/api", "http://127.0.0.1/?token=secret",
    "http://127.0.0.1/#frag", "http://127.0.0.1:0", "http://[::]", "http://[ff02::1]", "http://[fe80::1%3]"])
def test_mcp_api_origin_rejects_nonlocal_or_nonorigin(url):
    with pytest.raises(McpConfigError):
        McpConfig(url, TOKEN)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://10.2.3.4", "https://192.168.1.2", "http://[::1]:8000", "http://[fd00::2]"])
def test_mcp_local_origins_are_pinned(url):
    cfg = McpConfig(url, TOKEN)
    assert cfg.connect_url.startswith(("http://", "https://"))
    assert TOKEN not in repr(cfg)


def test_mcp_all_dns_answers_checked_and_connection_pinned():
    def answer(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (v, port)) for v in ("10.0.0.2", "8.8.8.8")]
    with pytest.raises(McpConfigError):
        _endpoint("http://device.test:8000", answer)
    endpoint, host, sni = _endpoint("https://device.test", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.3", 443))])
    assert (endpoint, host, sni) == ("https://192.168.0.3:443", "device.test:443", "device.test")


def test_mcp_dedicated_env_token_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"STEPEYE_MCP_TOKEN={TOKEN}\n")
    monkeypatch.delenv("STEPEYE_MCP_TOKEN", raising=False)
    with pytest.raises(McpConfigError):
        McpConfig.from_env()
    monkeypatch.setenv("STEPEYE_MCP_TOKEN", "ordinary_session_token")
    with pytest.raises(McpConfigError):
        McpConfig.from_env()
    monkeypatch.setenv("STEPEYE_MCP_TOKEN", TOKEN)
    assert McpConfig.from_env().enable_images is False
    monkeypatch.setenv("STEPEYE_MCP_ENABLE_IMAGES", "yes")
    with pytest.raises(McpConfigError):
        McpConfig.from_env()


def test_mcp_proxy_get_only_and_carries_token_on_same_origin():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"id": "scene1"}])
    api = StepEyeApi(config(), transport=httpx.MockTransport(handler))
    result = asyncio.run(api.get("/api/mcp/scenes"))
    assert result == [{"id": "scene1"}]
    assert seen[0].method == "GET"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert str(seen[0].url) == "http://127.0.0.1:8000/api/mcp/scenes"
    for path in ("https://evil.test", "//evil.test", "/api/auth/users", "/api/label-assets/a/confirm", "/api/mcp/tokens"):
        with pytest.raises(ApiError, match="Unsupported"):
            asyncio.run(api.get(path))
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 500])
def test_mcp_errors_are_sanitized_and_redirects_not_followed(status):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers={"location": "http://8.8.8.8/private"}, text=TOKEN)
    api = StepEyeApi(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError) as error:
        asyncio.run(api.get("/api/mcp/scenes"))
    assert TOKEN not in str(error.value)
    assert "8.8.8.8" not in str(error.value)
    assert len(seen) == 1


@pytest.mark.parametrize("payload,content_type", [(b"x" * 1025, "application/json"), (b"not json", "application/json"), (b"{}", "text/html")])
def test_mcp_caps_and_validates_upstream_json(payload, content_type):
    api = StepEyeApi(config(max_response_bytes=1024), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=payload, headers={"content-type": content_type})))
    with pytest.raises(ApiError):
        asyncio.run(api.get("/api/mcp/scenes"))


def test_mcp_concurrency_is_bounded_and_each_read_reauthorizes():
    active, peak, requests, revoked = 0, 0, 0, False
    async def handler(request):
        nonlocal active, peak, requests
        requests += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return httpx.Response(401 if revoked else 200, json=[])
    api = StepEyeApi(config(max_concurrent=2), transport=httpx.MockTransport(handler))
    async def scenario():
        nonlocal revoked
        await asyncio.gather(*(api.get("/api/mcp/scenes") for _ in range(5)))
        revoked = True
        with pytest.raises(ApiError, match="Access denied"):
            await api.get("/api/mcp/scenes")
    asyncio.run(scenario())
    assert peak == 2 and requests == 6


def test_mcp_total_timeout_includes_wait_and_body():
    async def handler(request):
        await asyncio.sleep(.1)
        return httpx.Response(200, json=[])
    api = StepEyeApi(config(timeout_s=.01), transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError, match="timed out"):
        asyncio.run(api.get("/api/mcp/scenes"))


def test_mcp_image_opt_in_and_media_guard():
    data = b"\x89PNG\r\n\x1a\nfixture"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=data, headers={"content-type": "image/png"}))
    with pytest.raises(ApiError, match="disabled"):
        asyncio.run(StepEyeApi(config(), transport=transport).get("/api/label-assets/a/image", image=True))
    image = asyncio.run(StepEyeApi(config(enable_images=True), transport=transport).get("/api/label-assets/a/image", image=True))
    assert image == (data, "image/png")
    invalid = httpx.MockTransport(lambda r: httpx.Response(200, content=b"<svg>", headers={"content-type": "image/svg+xml"}))
    with pytest.raises(ApiError, match="Unsupported image"):
        asyncio.run(StepEyeApi(config(enable_images=True), transport=invalid).get("/api/label-assets/a/image", image=True))


def test_mcp_ids_and_pagination():
    for value in ("../a", "a?b", "a/b", "", "a%2fb"):
        with pytest.raises(ApiError):
            _identifier(value)
    assert _identifier("abc_01-z") == "abc_01-z"
    assert _page([1, 2, 3], 2, 0) == {"items": [1, 2], "total": 3, "offset": 0, "has_more": True, "next_offset": 2}
    for limit, offset in ((0, 0), (101, 0), (1, -1), (True, 0)):
        with pytest.raises(ApiError):
            _page([], limit, offset)
