"""Static external connector policy and pre-parse wire bounds (no optional SDK needed)."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from visual.mcp_connectors import (ConnectorError, McpConnectors, _BoundedStream, _arguments,
                                   _loopback_url, load_connectors)


def write_config(tmp_path, **changes):
    row = {"enabled": True, "transport": "streamable-http", "url": "http://127.0.0.1:9100/mcp",
           "bearer_env": "EXTERNAL_DOCS_TOKEN", "allow_tools": {"search": ["query", "limit"]}}
    row.update(changes)
    path = tmp_path / "mcp-connectors.json"
    path.write_text(json.dumps({"version": 1, "connectors": {"docs": row}}))
    return path


@pytest.mark.parametrize("url", ["https://example.com/mcp", "http://192.168.0.3/mcp", "http://localhost/mcp", "file:///tmp/mcp",
    "http://127.0.0.1/mcp?secret=x", "http://u:p@127.0.0.1/mcp", "http://127.0.0.1:0/mcp", "http://[::]/mcp"])
def test_mcp_connector_only_fixed_literal_loopback_url(url):
    with pytest.raises(ConnectorError):
        _loopback_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9100/mcp", "https://[::1]/mcp"])
def test_mcp_connector_loopback_url_allowed(url):
    assert _loopback_url(url) == url


@pytest.mark.parametrize("changes", [{"enabled": "yes"}, {"command": "cmd.exe"}, {"transport": "stdio"},
    {"bearer_env": "STEPEYE_MCP_TOKEN"}, {"allow_tools": {}}, {"allow_tools": {"search": ["image"]}},
    {"max_response_bytes": 10**10}, {"timeout_s": float("nan")}, {"url": "http://8.8.8.8/mcp"}])
def test_mcp_connector_rejects_shell_arbitrary_urls_and_unsafe_policy(tmp_path, changes):
    with pytest.raises(ConnectorError):
        load_connectors(write_config(tmp_path, **changes))


def test_mcp_connector_config_path_and_cap(tmp_path):
    with pytest.raises(ConnectorError):
        load_connectors(Path("relative.json"))
    path = tmp_path / "big.json"
    path.write_bytes(b" " * 65537)
    with pytest.raises(ConnectorError):
        load_connectors(path)


def test_mcp_connector_only_small_scalar_metadata(tmp_path):
    cfg = load_connectors(write_config(tmp_path))["docs"]
    assert _arguments(cfg, "search", {"query": "safety labels", "limit": 10})["limit"] == 10
    for tool, args in (("write", {}), ("search", {"image": "x"}), ("search", {"query": ["nested"]}),
                       ("search", {"query": "data:image/png;base64,aaa"}), ("search", {"query": "x" * 513}),
                       ("search", {"query": "file:///tmp/picture.png"}), ("search", {"limit": float("inf")}),
                       ("search", {"limit": 2**60})):
        with pytest.raises(ConnectorError):
            _arguments(cfg, tool, args)


def test_mcp_connector_disable_applies_to_next_request(tmp_path):
    path = write_config(tmp_path)
    client = McpConnectors(path)
    assert client._get("docs").enabled
    write_config(tmp_path, enabled=False)
    with pytest.raises(ConnectorError, match="disabled"):
        client._get("docs")
    with pytest.raises(ConnectorError):
        client._get("unknown")


def test_mcp_connector_caps_raw_stream_before_sdk_parsing():
    closed = []
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{" * 1000
            yield b"x" * 1000
        async def aclose(self):
            closed.append(True)
    async def scenario():
        stream = _BoundedStream(Chunks(), 1024)
        received = []
        try:
            with pytest.raises(httpx.TransportError, match="wire size"):
                async for block in stream:
                    received.append(block)
        finally:
            await stream.aclose()
        assert len(received) == 1 and len(received[0]) == 1000
    asyncio.run(scenario())
    assert closed == [True]
