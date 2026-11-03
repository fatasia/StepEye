"""Official ClientSession initialize/list/call over a real local external MCP server."""
import asyncio
from contextlib import contextmanager
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("mcp.server.fastmcp", reason="Optional official MCP SDK")
if version("mcp").split(".")[0] != "1":
    pytest.skip("Requires official SDK v1.30.0", allow_module_level=True)

from visual.mcp_connectors import ConnectorError, McpConnectors

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_TOKEN = "external_fixture_only_credential"


@contextmanager
def external_server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    source = tmp_path / "trusted_fixture.py"
    source.write_text('''import asyncio, os, sys
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ImageContent
from visual.mcp_server import _BearerOnly
port = int(sys.argv[1])
mcp = FastMCP("external_fixture", json_response=True, stateless_http=True, log_level="CRITICAL",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[f"http://127.0.0.1:{port}"]))
@mcp.tool(annotations={"readOnlyHint": True})
async def read_meta(query: str = "", delay: float = 0, size: int = 1) -> dict[str, object]:
    await asyncio.sleep(delay)
    return {"query": query, "credential": os.environ["EXTERNAL_TEST_TOKEN"], "payload": "x" * size}
@mcp.tool(annotations={"readOnlyHint": True})
def hidden_write() -> str:
    raise RuntimeError("This must never run, even though its hint says read-only")
@mcp.tool()
def read_image():
    return ImageContent(type="image", mimeType="image/png", data="aW1hZ2U=")
uvicorn.run(_BearerOnly(mcp.streamable_http_app(), os.environ["EXTERNAL_TEST_TOKEN"]),
    host="127.0.0.1", port=port, log_level="critical", access_log=False)
''')
    with (tmp_path / "external-stderr.txt").open("w+", encoding="utf8") as stderr:
        process = subprocess.Popen([sys.executable, str(source), str(port)], cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "EXTERNAL_TEST_TOKEN": EXTERNAL_TOKEN, "PYTHONUTF8": "1"},
            stdout=subprocess.PIPE, stderr=stderr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            with httpx.Client(trust_env=False, timeout=.5) as http:
                for _ in range(100):
                    if process.poll() is not None:
                        stderr.seek(0); pytest.fail(stderr.read())
                    try:
                        assert http.get(url).status_code == 401; break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        time.sleep(.05)
                else:
                    pytest.fail("External fixture did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            assert process.stdout.read() == b""
            process.stdout.close()
            stderr.seek(0)
            assert EXTERNAL_TOKEN not in stderr.read()


def configured(tmp_path, url, **changes):
    row = {"enabled": True, "transport": "streamable-http", "url": url, "bearer_env": "EXTERNAL_TEST_TOKEN",
           "allow_tools": {"read_meta": ["query", "delay", "size"], "read_image": []}, "timeout_s": 5}
    row.update(changes)
    path = tmp_path / "trusted-connectors.json"
    path.write_text(json.dumps({"version": 1, "connectors": {"fixture": row}}))
    return path


def test_mcp_external_real_handshake_filter_call_revocation_disable(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTERNAL_TEST_TOKEN", EXTERNAL_TOKEN)
    with external_server(tmp_path) as url:
        path = configured(tmp_path, url)
        client = McpConnectors(path)
        async def scenario():
            tools = await client.list_tools("fixture")
            assert {t["name"] for t in tools["tools"]} == {"read_meta", "read_image"}
            result = await client.call("fixture", "read_meta", {"query": "plain metadata"})
            assert result["data"]["query"] == "plain metadata"
            assert result["data"]["credential"] == "[redacted]"
            assert EXTERNAL_TOKEN not in str(result)
            with pytest.raises(ConnectorError, match="allowlist"):
                await client.call("fixture", "hidden_write", {})
            with pytest.raises(ConnectorError, match="attachments"):
                await client.call("fixture", "read_image", {})
            # Explicit bearer only; no session or StepEye token fallback.
            monkeypatch.setenv("EXTERNAL_TEST_TOKEN", "wrong_external_fixture_token")
            with pytest.raises(ConnectorError, match="request failed"):
                await client.call("fixture", "read_meta", {})
            monkeypatch.setenv("EXTERNAL_TEST_TOKEN", EXTERNAL_TOKEN)
            configured(tmp_path, url, enabled=False)
            with pytest.raises(ConnectorError, match="disabled"):
                await client.call("fixture", "read_meta", {})
        asyncio.run(scenario())


def test_mcp_external_wire_cap_and_total_timeout_are_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTERNAL_TEST_TOKEN", EXTERNAL_TOKEN)
    with external_server(tmp_path) as url:
        path = configured(tmp_path, url, max_response_bytes=4096)
        async def scenario():
            with pytest.raises(ConnectorError, match="time/size"):
                await McpConnectors(path).call("fixture", "read_meta", {"size": 10000})
            configured(tmp_path, url, timeout_s=.2)
            with pytest.raises(ConnectorError, match="time/size"):
                await McpConnectors(path).call("fixture", "read_meta", {"delay": 1})
        asyncio.run(scenario())
