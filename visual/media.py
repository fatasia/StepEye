"""Monibuca 信令桥：浏览器只访问 StepEye，服务地址及凭据留在服务端。"""
from __future__ import annotations

import os
import secrets
import threading
import time
import queue
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx


class MediaError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class MediaBridge:
    def __init__(self, base: str = "", prefix: str = "/webrtc/whep", transport=None, lease_s: float = 180, clock=time.monotonic):
        self.base = base.rstrip("/")
        self.prefix = prefix.rstrip("/")
        if self.base:
            u = urlsplit(self.base)
            if u.scheme not in ("http", "https") or not u.netloc or u.username or u.query or u.fragment:
                raise ValueError("MONIBUCA_URL 必须是不含凭据和查询参数的 HTTP(S) 地址")
        if self.prefix not in ("/webrtc/whep", "/webrtc/play"):
            raise ValueError("MONIBUCA_WHEP_PREFIX 仅支持 /webrtc/whep(v6) 或 /webrtc/play(v5)")
        self.transport = transport
        self.lease_s, self.clock = lease_s, clock
        self._lock = threading.Condition()
        self._sweeper: threading.Thread | None = None
        self._closed = False
        self._sessions: dict[str, tuple[str, str, str, float]] = {}
        self._pending = 0

    def _wake_sweeper_locked(self) -> None:
        # Session insertion and worker retirement use the same lock.
        if self._sweeper is None:
            self._sweeper = threading.Thread(target=self._sweep, name="preview-leases", daemon=True)
            self._sweeper.start()
        self._lock.notify_all()

    def _sweep(self) -> None:
        while True:
            with self._lock:
                if self._closed or not self._sessions:
                    self._sweeper = None
                    return
                remaining = min(s[3] for s in self._sessions.values()) - self.clock()
                if remaining > 0:
                    self._lock.wait(timeout=remaining)
                    continue
            self.expire_sessions()

    def expire_sessions(self) -> None:
        with self._lock:
            expired = [token for token, s in self._sessions.items() if s[3] <= self.clock()]
            paths = [self._sessions.pop(token)[2] for token in expired]
            self._lock.notify_all()
        if paths:
            def cleanup():
                for path in paths:
                    try:
                        self.request("DELETE", path, timeout=2)
                    except MediaError:
                        pass
            threading.Thread(target=cleanup, name="preview-lease-cleanup", daemon=True).start()

    @classmethod
    def from_env(cls):
        from .llm_provider import _default_env_path, load_env_file
        values = load_env_file(_default_env_path())
        return cls(os.getenv("MONIBUCA_URL", values.get("MONIBUCA_URL", "")).strip(),
                   os.getenv("MONIBUCA_WHEP_PREFIX", values.get("MONIBUCA_WHEP_PREFIX", "/webrtc/whep")))

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self.base:
            raise MediaError("未配置 MONIBUCA_URL，当前使用分析画面", 503)
        try:
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False,
                              transport=self.transport) as client:
                res = client.request(method, self.base + path, **kwargs)
            if res.status_code >= 300:
                raise MediaError(f"Monibuca 请求失败（HTTP {res.status_code}），请检查流和服务配置")
            return res
        except httpx.TimeoutException:
            raise MediaError("Monibuca 响应超时，请检查服务和流是否在线", 504) from None
        except httpx.HTTPError:
            raise MediaError("无法连接 Monibuca，请检查服务和 MONIBUCA_URL", 502) from None

    def offer(self, stream: str, sdp: bytes, station: str, owner: str) -> tuple[bytes, str | None]:
        self.expire_sessions()
        with self._lock:
            if self._closed:
                raise MediaError("预览服务已停止", 503)
            if len(self._sessions) + self._pending >= 128:
                raise MediaError("预览会话已满，请关闭不用的预览后重试", 429)
            self._pending += 1
        try:
            return self._offer(stream, sdp, station, owner)
        finally:
            with self._lock:
                self._pending -= 1

    def _whep_target(self, stream: str) -> str:
        """v6(/webrtc/whep) 用 query 风格 ?app=&stream=（v6 beta 实测契约）；
        v5(/webrtc/play) 保留路径风格。stream 允许 app/name 或裸流名(默认 live)。"""
        if self.prefix != "/webrtc/whep":
            return f"{self.prefix}/{stream}"
        app, _, name = stream.rpartition("/")
        if not app:
            app, name = "live", stream
        return f"{self.prefix}?app={quote(app, safe='')}&stream={quote(name, safe='')}"

    def _offer(self, stream: str, sdp: bytes, station: str, owner: str) -> tuple[bytes, str | None]:
        path = self._whep_target(stream)
        res = self.request("POST", path, content=sdp, headers={"Content-Type": "application/sdp"})
        if len(res.content) > 256 * 1024 or not res.content.lstrip().startswith(b"v=0"):
            raise MediaError("Monibuca 未返回有效 SDP，请核对版本与 WHEP 路径")
        location = res.headers.get("location")
        token = None
        if location:
            url = urljoin(self.base + path, location)
            u, base = urlsplit(url), urlsplit(self.base)
            prefix = base.path.rstrip("/") + "/webrtc/"
            decoded = u.path
            for _ in range(3):
                decoded = unquote(decoded)
            if (u.scheme, u.netloc) != (base.scheme, base.netloc) or not u.path.startswith(prefix) or \
                    any(part in (".", "..") for part in decoded.split("/")) or "\\" in decoded:
                raise MediaError("Monibuca 返回了不允许的会话地址")
            token = secrets.token_urlsafe(24)
            with self._lock:
                closed = self._closed
                if not closed:
                    self._sessions[token] = (station, owner, url[len(self.base):], self.clock() + self.lease_s)
                    self._wake_sweeper_locked()
            if closed:
                try:
                    self.request("DELETE", url[len(self.base):], timeout=2)
                except MediaError:
                    pass
                raise MediaError("预览服务已停止", 503)
        return res.content, token

    def release(self, token: str, station: str, owner: str) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return
            if session[:2] != (station, owner):
                raise MediaError("该预览会话不属于当前用户或工位", 403)
        try:
            self.request("DELETE", session[2])
        finally:
            with self._lock:
                self._sessions.pop(token, None)
                self._lock.notify_all()

    def renew(self, token: str, station: str, owner: str) -> None:
        self.expire_sessions()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                raise MediaError("预览会话已过期，请重试", 404)
            if session[:2] != (station, owner):
                raise MediaError("该预览会话不属于当前用户或工位", 403)
            self._sessions[token] = (*session[:3], self.clock() + self.lease_s)
            self._lock.notify_all()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            paths = [s[2] for s in self._sessions.values()]
            self._sessions.clear()
            sweeper = self._sweeper
            self._lock.notify_all()
        if sweeper is not None:
            sweeper.join(timeout=1)
        jobs = queue.SimpleQueue()
        for path in paths:
            jobs.put(path)
        deadline = time.monotonic() + 3
        def cleanup():
            while time.monotonic() < deadline:
                try:
                    path = jobs.get_nowait()
                except queue.Empty:
                    return
                try:
                    self.request("DELETE", path, timeout=min(1, max(.01, deadline - time.monotonic())))
                except MediaError:
                    pass
        workers = [threading.Thread(target=cleanup, name="preview-shutdown", daemon=True)
                   for _ in range(min(4, len(paths)))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
