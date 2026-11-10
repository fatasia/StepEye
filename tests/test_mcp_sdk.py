"""Real official SDK transports against a local REST fixture, no vision dependencies."""
import asyncio
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from importlib.metadata import version
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest

pytest.importorskip("mcp.server.fastmcp", reason="Optional SDK; use requirements-mcp.txt in a separate venv")
if version("mcp").split(".")[0] != "1":
    pytest.skip("StepEye optional MCP adapter requires official SDK v1.30.0", allow_module_level=True)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

TOKEN = "se_mcp_" + "fixtureonly_not_a_real_credential"
ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"stepeye_list_scenes", "stepeye_list_label_projects", "stepeye_list_assets", "stepeye_get_annotations",
         "stepeye_get_label_job", "stepeye_get_quality_report"}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


@contextmanager
def api_fixture():
    state = {"revoked": False, "calls": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            state["calls"].append((self.command, self.path, self.headers.get("Authorization")))
            if state["revoked"] or self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.send_response(401); self.end_headers(); return
            path = urlsplit(self.path).path
            payloads = {
                "/api/mcp/scenes": [{"id": "s1", "name": "车间A"}],
                "/api/label-projects": [{"id": "p1", "name": "安全帽", "classes": ["helmet"]}],
                "/api/label-projects/p1/assets": {"items": [{"id": "a1", "project_id": "p1"}], "total": 1, "offset": 0},
                "/api/label-assets/a1/annotations": {"asset_id": "a1", "revision": 2, "status": "draft", "boxes": []},
                "/api/label-jobs/j1": {"id": "j1", "project_id": "p1", "status": "done"},
                "/api/label-projects/p1/quality": {"project_id": "p1", "confirmed": 0, "ready": False}}
            if path == "/api/label-assets/a1/image":
                body, mime = PNG, "image/png"
            elif path in payloads:
                body, mime = json.dumps(payloads[path]).encode(), "application/json"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def env_for(url, images=False):
    return {**os.environ, "STEPEYE_MCP_API_URL": url, "STEPEYE_MCP_TOKEN": TOKEN,
            "STEPEYE_MCP_ENABLE_IMAGES": "1" if images else "0", "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}


def output(result):
    assert not result.isError, result
    return result.structuredContent


async def exercise(session, state, images=False):
    initialized = await session.initialize()
    assert initialized.serverInfo.name == "stepeye_mcp"
    tools = (await session.list_tools()).tools
    assert {t.name for t in tools} == TOOLS | ({"stepeye_get_asset_image"} if images else set())
    for tool in tools:
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False
    scene = output(await session.call_tool("stepeye_list_scenes", {"limit": 1}))
    assert scene["items"][0]["id"] == "s1" and scene["has_more"] is False
    assert output(await session.call_tool("stepeye_list_label_projects"))["items"][0]["id"] == "p1"
    assert output(await session.call_tool("stepeye_list_assets", {"project_id": "p1", "limit": 1}))["items"][0]["id"] == "a1"
    assert output(await session.call_tool("stepeye_get_annotations", {"asset_id": "a1"}))["data"]["status"] == "draft"
    assert output(await session.call_tool("stepeye_get_label_job", {"job_id": "j1"}))["data"]["status"] == "done"
    assert output(await session.call_tool("stepeye_get_quality_report", {"project_id": "p1"}))["data"]["ready"] is False
    if images:
        response = await session.call_tool("stepeye_get_asset_image", {"asset_id": "a1"})
        assert not response.isError and response.content[0].type == "image"
        assert base64.b64decode(response.content[0].data) == PNG
    before = len(state["calls"])
    for name, args in (("stepeye_list_assets", {"project_id": "../p1"}), ("stepeye_list_scenes", {"limit": 101}),
                       ("stepeye_confirm_annotations", {"asset_id": "a1"})):
        assert (await session.call_tool(name, args)).isError
        assert len(state["calls"]) == before
    state["revoked"] = True
    denied = await session.call_tool("stepeye_list_label_projects")
    assert denied.isError and "Access denied" in denied.content[0].text
    assert TOKEN not in str(denied)
    assert len(state["calls"]) == before + 1
    assert all(method == "GET" and token == f"Bearer {TOKEN}" for method, path, token in state["calls"])


@pytest.mark.parametrize("images", [False, True])
def test_mcp_stdio_real_handshake_list_call_and_revocation(images, tmp_path):
    with api_fixture() as (url, state), (tmp_path / "stderr.txt").open("w+", encoding="utf8") as errlog:
        async def scenario():
            params = StdioServerParameters(command=sys.executable, args=["-m", "visual.mcp_server"], cwd=str(ROOT), env=env_for(url, images))
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await exercise(session, state, images)
        asyncio.run(scenario())
        errlog.seek(0)
        assert TOKEN not in errlog.read()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_mcp_streamable_http_real_handshake_and_origin_auth_guards(tmp_path):
    with api_fixture() as (url, state), (tmp_path / "http-stderr.txt").open("w+", encoding="utf8") as errlog:
        port = free_port()
        endpoint = f"http://127.0.0.1:{port}/mcp"
        process = subprocess.Popen([sys.executable, "-m", "visual.mcp_server", "--transport", "streamable-http", "--port", str(port)],
                                   cwd=ROOT, env=env_for(url), stdout=subprocess.PIPE, stderr=errlog,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(trust_env=False, timeout=.5) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        errlog.seek(0); pytest.fail("MCP HTTP subprocess exited: " + errlog.read())
                    try:
                        assert client.get(endpoint).status_code == 401
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        time.sleep(.05)
                else:
                    pytest.fail("MCP HTTP subprocess did not start")
                headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
                body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
                assert client.post(endpoint, headers={**headers, "Origin": "http://evil.test"}, json=body).status_code == 403
                assert client.post(endpoint, headers={**headers, "Host": "evil.test"}, json=body).status_code == 421
                assert client.post(endpoint, headers={**headers, "Authorization": "Bearer wrong"}, json=body).status_code == 401
            async def scenario():
                async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5, trust_env=False) as http:
                    async with streamable_http_client(endpoint, http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await exercise(session, state)
            asyncio.run(scenario())
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            assert process.stdout.read() == b""
            process.stdout.close()
        errlog.seek(0)
        assert TOKEN not in errlog.read()

def test_optional_proposal_tool_calls_only_agent_draft():
    from visual.mcp_server import McpConfig, create_server

    calls = []
    class Bridge:
        async def draft(self, aid, revision, targets):
            calls.append((aid, revision, targets))
            return {"state": "suggested", "producer": "agent"}

    async def run():
        proxy = create_server(McpConfig("http://127.0.0.1:8765", TOKEN, enable_drafts=True), api=Bridge())
        tools = {entry.name: entry for entry in await proxy.list_tools()}
        assert "stepeye_propose_annotation" in tools
        assert not tools["stepeye_propose_annotation"].annotations.readOnlyHint
        await proxy.call_tool("stepeye_propose_annotation", {"asset_id":"LA-test","expected_revision":3,"targets":[]})
        assert calls == [("LA-test", 3, [])]
    asyncio.run(run())
