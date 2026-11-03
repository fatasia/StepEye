"""Administrator-configured, read-only external MCP client (loopback HTTP only).

No HTTP management API, shell command, image upload, or automatic tool execution.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import timedelta
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx


class ConnectorError(RuntimeError):
    pass


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
_BLOCKED_ARGS = {"image", "images", "image_url", "image_path", "file", "files", "file_path", "path", "base64", "bytes", "data"}


def _loopback_url(value: str) -> str:
    try:
        url = urlsplit(value)
        address = ipaddress.ip_address(url.hostname or "")
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (url.scheme not in ("http", "https") or not address.is_loopback or url.username is not None
                or url.password is not None or url.query or url.fragment or not url.path.startswith("/")
                or any(c.isspace() for c in value) or "%" in (url.hostname or "")
                or len(value) > 2048 or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ConnectorError("External MCP URL must be an HTTP(S) literal loopback address and fixed path") from None


@dataclass(frozen=True)
class Connector:
    name: str
    enabled: bool
    url: str
    bearer_env: str
    allow_tools: dict[str, tuple[str, ...]]
    timeout_s: float = 10
    max_response_bytes: int = 256 * 1024


def load_connectors(path: Path) -> dict[str, Connector]:
    """Load an explicitly selected trusted local file. Never load config from tool output."""
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ConnectorError("Use an absolute path to a trusted local JSON configuration file")
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        config = json.loads(raw)
        if set(config) != {"version", "connectors"} or config["version"] != 1:
            raise ValueError
        rows = config["connectors"]
        if not isinstance(rows, dict) or len(rows) > 10:
            raise ValueError
        result = {}
        for name, row in rows.items():
            if not _NAME.fullmatch(name) or not isinstance(row, dict):
                raise ValueError
            if set(row) - {"enabled", "transport", "url", "bearer_env", "allow_tools", "timeout_s", "max_response_bytes"}:
                raise ValueError
            if type(row.get("enabled")) is not bool or row.get("transport") != "streamable-http":
                raise ValueError
            token_env = row.get("bearer_env", "")
            if token_env and (not _ENV.fullmatch(token_env) or token_env == "STEPEYE_MCP_TOKEN"):
                raise ValueError
            tools = row.get("allow_tools")
            if not isinstance(tools, dict) or not 1 <= len(tools) <= 30:
                raise ValueError
            allow = {}
            for tool, args in tools.items():
                if (not _TOOL.fullmatch(tool) or not isinstance(args, list) or len(args) > 20
                        or not all(isinstance(k, str) and _TOOL.fullmatch(k) and k.lower() not in _BLOCKED_ARGS for k in args)
                        or len(set(args)) != len(args)):
                    raise ValueError
                allow[tool] = tuple(args)
            timeout, cap = row.get("timeout_s", 10), row.get("max_response_bytes", 256 * 1024)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not .05 <= timeout <= 30:
                raise ValueError
            if type(cap) is not int or not 1024 <= cap <= 1024 * 1024:
                raise ValueError
            result[name] = Connector(name, row["enabled"], _loopback_url(row["url"]), token_env, allow, timeout, cap)
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ConnectorError("Invalid connector configuration; check version, fixed loopback URL and explicit tool/argument allowlists") from None


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, source, cap):
        self.source, self.cap = source, cap

    async def __aiter__(self):
        count = 0
        async for block in self.source:
            count += len(block)
            if count > self.cap:
                raise httpx.TransportError("External MCP response exceeds the configured wire size limit")
            yield block

    async def aclose(self):
        await self.source.aclose()


class _BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self, url, cap):
        u = httpx.URL(url)
        self.origin = (u.scheme, u.host, u.port)
        self.cap = cap
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        u = request.url
        if (u.scheme, u.host, u.port) != self.origin:
            raise httpx.TransportError("External MCP origin change blocked")
        response = await self.inner.handle_async_request(request)
        if (300 <= response.status_code < 400
                or response.headers.get("content-encoding", "identity").lower() not in ("", "identity")):
            await response.aclose()
            raise httpx.TransportError("External MCP redirects and compressed responses are disabled")
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=_BoundedStream(response.stream, self.cap), extensions=response.extensions)

    async def aclose(self):
        await self.inner.aclose()


def _arguments(connector: Connector, tool: str, arguments: dict) -> dict:
    if tool not in connector.allow_tools:
        raise ConnectorError("Tool is not in this connector's administrator allowlist")
    if not isinstance(arguments, dict) or set(arguments) - set(connector.allow_tools[tool]):
        raise ConnectorError("Only explicitly allowlisted metadata arguments are accepted")
    for key, value in arguments.items():
        if key.lower() in _BLOCKED_ARGS or type(value) not in (str, int, float, bool, type(None)):
            raise ConnectorError("Only small scalar metadata arguments are accepted; files/images/nested payloads are disabled")
        if isinstance(value, str) and (len(value) > 512 or value.lower().startswith(("data:", "file:", "http://", "https://"))):
            raise ConnectorError("URLs, files, images and long encoded payloads are disabled")
        if type(value) is float and not math.isfinite(value):
            raise ConnectorError("Non-finite metadata is not accepted")
        if type(value) is int and abs(value) > 2**53:
            raise ConnectorError("Integer metadata is out of range")
    if len(json.dumps(arguments).encode()) > 8192:
        raise ConnectorError("Metadata arguments exceed 8 KiB")
    return arguments


class McpConnectors:
    """Reload config before every operation so a file edit disables the next request."""
    def __init__(self, path: Path, *, max_concurrent=2):
        self.path = Path(path)
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= 4:
            raise ConnectorError("External MCP concurrency must be 1–4")
        self._slots = asyncio.Semaphore(max_concurrent)

    def _get(self, name):
        connector = load_connectors(self.path).get(name)
        if connector is None or not connector.enabled:
            raise ConnectorError("Connector is missing or disabled in the local administrator configuration")
        return connector

    async def _request(self, name, tool=None, arguments=None):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError:
            raise ConnectorError("Install requirements-mcp.txt in the separate MCP environment") from None
        config = self._get(name)
        if tool is not None:
            arguments = _arguments(config, tool, arguments)
        token = os.environ.get(config.bearer_env, "") if config.bearer_env else ""
        if config.bearer_env and (not 16 <= len(token) <= 4096 or any(ord(c) < 32 or ord(c) > 126 for c in token)):
            raise ConnectorError("Set the connector's dedicated bearer environment variable in this process")
        headers = {"Accept-Encoding": "identity"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with asyncio.timeout(config.timeout_s):
                async with self._slots:
                    # Disable can take effect while a request waits for the semaphore.
                    current = self._get(name)
                    if current != config:
                        raise ConnectorError("Connector configuration changed; retry after reviewing it")
                    async with httpx.AsyncClient(headers=headers, trust_env=False, follow_redirects=False,
                            timeout=config.timeout_s, transport=_BoundedTransport(config.url, config.max_response_bytes)) as http:
                        async with streamable_http_client(config.url, http_client=http) as (read, write, _):
                            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=config.timeout_s)) as session:
                                await session.initialize()
                                if tool is None:
                                    result = await session.list_tools()
                                    value = {"connector": name, "tools": [t.model_dump(mode="json", exclude_none=True)
                                        for t in result.tools if t.name in config.allow_tools]}
                                else:
                                    result = await session.call_tool(tool, arguments)
                                    if result.isError:
                                        raise ConnectorError("External MCP tool failed; inspect the trusted server locally")
                                    if any(c.type != "text" for c in result.content):
                                        raise ConnectorError("External images, audio and resource attachments are disabled")
                                    value = {"connector": name, "tool": tool,
                                             "content": [c.text for c in result.content], "data": result.structuredContent}
                                raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
                                if len(raw.encode()) > config.max_response_bytes:
                                    raise ConnectorError("External MCP result exceeds the output limit")
                                # A misconfigured server may echo its credential; never expose it to another agent.
                                encoded_token = json.dumps(token, ensure_ascii=False)[1:-1]
                                return json.loads(raw.replace(encoded_token, "[redacted]")) if token else value
        except ConnectorError:
            raise
        except Exception as error:
            def policy_error(exc):
                if isinstance(exc, ConnectorError):
                    return exc
                if isinstance(exc, BaseExceptionGroup):
                    for child in exc.exceptions:
                        found = policy_error(child)
                        if found is not None:
                            return found
                return None
            known = policy_error(error)
            if known is not None:
                raise ConnectorError(str(known)) from None
            # SDK exceptions may include raw remote content; do not echo them.
            raise ConnectorError("External MCP request failed or exceeded a time/size limit; verify the local server and allowlist") from None

    async def list_tools(self, name):
        return await self._request(name)

    async def call(self, name, tool, arguments):
        return await self._request(name, tool, arguments)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Explicit read-only calls to administrator-named local MCP servers")
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list-connectors")
    for action in ("list-tools", "call"):
        command = sub.add_parser(action)
        command.add_argument("name")
        if action == "call":
            command.add_argument("tool")
            command.add_argument("--arguments", default="{}", help="Small scalar JSON metadata; never image/file content")
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
    try:
        if args.action == "list-connectors":
            value = {"connectors": [{"name": c.name, "enabled": c.enabled, "tools": list(c.allow_tools)}
                                   for c in load_connectors(args.config).values()]}
        else:
            client = McpConnectors(args.config)
            if args.action == "list-tools":
                value = asyncio.run(client.list_tools(args.name))
            else:
                if len(args.arguments.encode()) > 8192:
                    raise ConnectorError("Metadata arguments exceed 8 KiB")
                value = asyncio.run(client.call(args.name, args.tool, json.loads(args.arguments)))
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (ConnectorError, ValueError):
        print("Connector request rejected; check the trusted local configuration, enabled flag and metadata allowlist", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
