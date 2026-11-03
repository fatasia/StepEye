"""HTTP / WebSocket 服务：REST 查询、MJPEG 视频流、证据图片、实时事件推送，并托管 web/ 静态界面。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid

import cv2
import numpy as np
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .alerts import AlertStore
from .auth import AuthError, AuthStore
from .dsl import StationConfig, load_config
from .evidence import CycleStore
from .events import SIGNAL, Event
from .inspection import InspectionService, InspectionStore, load_scenarios
from .llm_provider import ProviderConfig, VLMProvider
from .labels import LabelError, LabelStore
from .label_jobs import LabelEngines, LabelJobs
from .label_routes import check_mcp_read, router as label_router
from .media import MediaBridge, MediaError
from .patrol import LOCAL_TZ, PatrolPlan, PatrolStore
from .recordings import RecordingIndex, parse_time
from .fusion import SignalAuditStore, SignalError, SignalInbox, SignalPacket
from .runtime import StationRuntime
from .shadow import ShadowStore, start_shadow_recorder
from .templates import list_templates, render_station

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
SAFE_NAME = re.compile(r"^[\w.\-]+$")
PUSH_KINDS = {"cycle_start", "step_done", "violation", "cycle_end", "quality", "calibrated", "safety"}


class Hub:
    """把运行时线程里的事件安全地广播给所有 WebSocket 客户端（慢客户端丢消息，不拖累采集线程）。"""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def publish(self, msg: dict[str, Any]) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        for q in list(self.clients):
            loop.call_soon_threadsafe(self._put, q, msg)

    @staticmethod
    def _put(q: asyncio.Queue, msg: dict[str, Any]) -> None:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def rewrite_station_zones(path: Path, zones: list[dict[str, Any]]) -> None:
    """把编辑后的 zones 写回工位 YAML(其余键原样保留;YAML 注释会丢失)。"""
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["zones"] = zones
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


class StationManager:
    def __init__(self, config_paths: list[Path], data_dir: Path, hub: Hub,
                 inspection_config: Path | None = None, vlm_transport=None,
                 provider_cfg: ProviderConfig | None = None,
                 alert_store: AlertStore | None = None, shadow: bool = False):
        from .audit import AuditStore
        self.audit = AuditStore(data_dir / "operations.db")
        self.data_dir = data_dir
        self.hub = hub
        self.store = CycleStore(data_dir / "sop.db")
        self.alerts = alert_store or AlertStore(data_dir / "alerts.db")
        self.feishu_webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
        from .notifications import NotificationDispatcher
        from .llm_provider import _default_env_path
        self.notifications = NotificationDispatcher.from_env(_default_env_path())
        self.shadow_store: ShadowStore | None = None
        from .geo import GeoStore
        self.geo = GeoStore(data_dir / "geo.db")
        self.patrol = PatrolStore(data_dir / "patrol.db")
        self.signal_store = SignalAuditStore(data_dir / "signals.db")
        self.signal_store.recover_abandoned()
        self.labels = LabelStore(data_dir / "labeling")
        self.label_engines = LabelEngines()
        # 只登记已验证布局/来源的现有权重；不启用被淘汰的烟火模型。
        self.label_engines.register("community-helmet", ROOT / "models/community/helmet.onnx",
                                    ["With Helmet", "Without Helmet"], "安全帽 ONNX · 现有评估权重")
        from .public_models import register_public_models
        register_public_models(self.label_engines, ROOT)
        self.label_jobs = LabelJobs(data_dir / "labeling/jobs.db", self.labels, self.label_engines)
        from .training_jobs import TrainingJobs
        self.training = TrainingJobs.from_env(data_dir / "training", self.labels, self.label_engines)
        self.evidence_root = data_dir / "evidence"
        self.runtimes: dict[str, StationRuntime] = {}
        self.paths: dict[str, Path] = {}
        self.threads: list[threading.Thread] = []
        self._threads: dict[str, threading.Thread] = {}
        # 拍照即检:给了配置且有凭据才启用;没凭据时端点返回明确指引,而不是悄悄坏掉
        self.inspection: InspectionService | None = None
        if inspection_config is not None:
            cfg = provider_cfg or ProviderConfig.from_env()
            if cfg.configured:
                self.inspection = InspectionService(
                    load_scenarios(inspection_config), InspectionStore(data_dir / "sop.db"),
                    data_dir / "inspection", VLMProvider(cfg, vlm_transport),
                    publish=self._on_inspection_msg, refs_root=data_dir)
            else:
                print("提示:未配置云端凭据或本地VLM，拍照即检不可用，其余功能不受影响")
        for p in config_paths:
            cfg: StationConfig = load_config(p)
            if shadow:
                cfg.source.shadow = True  # serve --shadow:全工位强制影子模式
            if cfg.station in self.runtimes:
                raise ValueError(f"工位 id 重复: {cfg.station}")
            rt = StationRuntime(cfg, data_dir, base_dir=ROOT, store=self.store)
            rt.frame_observers.append(self.patrol.observe_runtime)
            rt.signal_inbox = SignalInbox(self.signal_store, scope=cfg.station)
            rt.listeners.append(self._forwarder(cfg.station))
            if cfg.source.shadow:
                if self.shadow_store is None:
                    self.shadow_store = ShadowStore(data_dir / "shadow.db")
                start_shadow_recorder(rt, self.shadow_store)
            self.runtimes[cfg.station] = rt
            self.paths[cfg.station] = p
            self._register_label_engine(cfg)

    def _register_label_engine(self, cfg: StationConfig) -> None:
        if cfg.detector.enabled and Path(cfg.detector.model).suffix.lower() == ".onnx":
            from .onnx_detector import COCO80
            self.label_engines.register(f"station-{cfg.station}", ROOT / cfg.detector.model,
                list(cfg.detector.class_names) or list(COCO80), f"{cfg.station} · 工位 ONNX")

    def _forwarder(self, sid: str):
        def fwd(kind: str, payload: dict[str, Any]) -> None:
            if kind in PUSH_KINDS:
                self.hub.publish({"type": "event", "station": sid, "kind": kind, "data": payload})
            if kind == "violation":  # SOP 违规入告警中心(按周期+类型防重)
                rec, new = self.alerts.create(sid, "sop", payload["kind"], payload["severity"],
                                              payload["message"], ref_id=f"{payload.get('cycle_id')}:{payload['kind']}")
                if new:
                    self._push_out(rec)
            elif kind == "safety":  # 常驻安全告警(执行器已自带再武装,不防重)
                rec, new = self.alerts.create(sid, "safety", payload["rule"], payload["severity"], payload["message"])
                if new:
                    self._push_out(rec)
        return fwd

    def _on_inspection_msg(self, msg: dict[str, Any]) -> None:
        """拍照即检事件:推送 WS;NG/失败结论入告警中心;结论进影子库供真值标注。"""
        self.hub.publish(msg)
        d = msg.get("data", {})
        if msg.get("kind") == "inspection" and d.get("status") == "done":
            if self.shadow_store is not None:
                self.shadow_store.add("inspection", d.get("scenario", ""), d.get("id", ""),
                                      str(d.get("verdict")), d.get("confidence"),
                                      {"reason": d.get("reason", "")})
        if msg.get("kind") == "inspection" and d.get("status") == "done" and d.get("verdict") == "NG":
            rec, new = self.alerts.create(d.get("scenario", "inspection"), "inspection", "NG",
                                          "warn", f"拍照即检判 NG:{d.get('reason', '')}", ref_id=d.get("id"))
            if new:
                self._push_out(rec)

    def _push_out(self, rec: dict[str, Any]) -> None:
        """告警先广播，再异步通知；新渠道配置优先，旧飞书仅作兼容。"""
        self.hub.publish({"type": "alert", "data": rec})
        # New channel configuration takes precedence, including invalid configuration:
        # never silently send to a different destination after a configuration error.
        if self.notifications.configured:
            self.notifications.submit(rec)
        elif self.feishu_webhook and not rec.get("pushed"):
            def send():
                if _feishu_send(self.feishu_webhook,
                                f"[视觉告警] {rec['station']} {rec['kind']}\n{rec['message']}\n状态: 待处理"):
                    self.alerts.mark_pushed(rec["id"])
            threading.Thread(target=send, name="feishu", daemon=True).start()

    def add_station(self, path: Path) -> str:
        """热加岗:运行中加载新工位 YAML(向导部署用),无需重启。"""
        cfg: StationConfig = load_config(path)
        if cfg.station in self.runtimes:
            raise ValueError(f"工位 id 已存在:{cfg.station}")
        rt = StationRuntime(cfg, self.data_dir, base_dir=ROOT, store=self.store)
        rt.frame_observers.append(self.patrol.observe_runtime)
        rt.signal_inbox = SignalInbox(self.signal_store, scope=cfg.station)
        rt.listeners.append(self._forwarder(cfg.station))
        if cfg.source.shadow and self.shadow_store is None:
            self.shadow_store = ShadowStore(self.data_dir / "shadow.db")
        if cfg.source.shadow:
            start_shadow_recorder(rt, self.shadow_store)
        self.runtimes[cfg.station] = rt
        self.paths[cfg.station] = path
        self._register_label_engine(cfg)
        t = threading.Thread(target=rt.run, name=f"station-{cfg.station}", daemon=True)
        t.start()
        self.threads.append(t)
        self._threads[cfg.station] = t
        self.hub.publish({"type": "event", "station": cfg.station, "kind": "station_added", "data": {"ok": True}})
        return cfg.station

    def start(self) -> None:
        self.label_jobs.start()
        if self.inspection is not None:
            self.inspection.start()
        for sid, rt in self.runtimes.items():
            t = threading.Thread(target=rt.run, name=f"station-{sid}", daemon=True)
            t.start()
            self.threads.append(t)
            self._threads[sid] = t

    def reload(self, sid: str) -> None:
        """配置变更后热重载:停旧线程,按 YAML 重建运行时(证据与库共享)。"""
        old = self.runtimes.get(sid)
        if old is None:
            raise HTTPException(404, f"工位不存在:{sid}")
        old.stop()
        t = self._threads.get(sid)
        if t is not None:
            t.join(timeout=5)
        old.signal_inbox.close()
        cfg: StationConfig = load_config(self.paths[sid])
        if cfg.station != sid:
            raise HTTPException(400, f"配置中的 station id({cfg.station})与工位({sid})不一致,不能重载")
        rt = StationRuntime(cfg, self.data_dir, base_dir=ROOT, store=self.store)
        rt.frame_observers.append(self.patrol.observe_runtime)
        rt.signal_inbox = SignalInbox(self.signal_store, scope=cfg.station)
        rt.listeners.append(self._forwarder(sid))
        self.runtimes[sid] = rt
        nt = threading.Thread(target=rt.run, name=f"station-{sid}", daemon=True)
        nt.start()
        self.threads.append(nt)
        self._threads[sid] = nt
        self.hub.publish({"type": "event", "station": sid, "kind": "reloaded", "data": {"ok": True}})

    def stop(self) -> None:
        self.notifications.close()
        self.training.close()
        self.label_jobs.close()
        self.labels.close()
        for rt in self.runtimes.values():
            rt.stop()
        for t in self.threads:
            t.join(timeout=3)
        for rt in self.runtimes.values():
            rt.signal_inbox.close()
        if self.inspection is not None:
            self.inspection.stop(close_store=True)
        from .local_vlm import close_all
        close_all()
        self.store.close()
        self.patrol.close()
        self.signal_store.close()
        self.audit.close()

    def get(self, sid: str) -> StationRuntime:
        rt = self.runtimes.get(sid)
        if rt is None:
            raise HTTPException(404, f"工位不存在：{sid}")
        return rt


class CalibrateBody(BaseModel):
    zones: list[str] | None = None


class SignalBody(BaseModel):
    name: str
    value: Any = None


class ZonesBody(BaseModel):
    zones: list[dict[str, Any]]


class LoginBody(BaseModel):
    username: str
    password: str


class UserBody(BaseModel):
    username: str
    password: str
    role: str = "operator"


class AlertPatchBody(BaseModel):
    status: str | None = None
    comment: str | None = None


class SourceBody(BaseModel):
    type: str = "rtsp"
    uri: str = ""


class GeoBody(BaseModel):
        id: str
        name: str
        kind: str = "station"
        lat: float
        lng: float


class IntervalsBody(BaseModel):
    intervals: list[dict[str, Any]] = Field(max_length=256)
    extract: bool = True
    per_interval: int = Field(default=2, ge=1, le=8)


class PlanPointBody(BaseModel):
    name: str
    kind: str = "camera"
    x: float
    y: float
    station_id: str = ""


class OnvifDiscoverBody(BaseModel):
    timeout_s: float = Field(default=3.0, ge=1, le=15, allow_inf_nan=False)


class OnvifAdoptBody(BaseModel):
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9_.:-]+$")
    port: int = Field(default=80, ge=1, le=65535)
    user: str
    password: str


class TruthBody(BaseModel):
    truth: str


class CompileBody(BaseModel):
    text: str


class StepsBody(BaseModel):
    steps: list[dict[str, Any]]


class LabelEvidenceBody(BaseModel):
    kind: str = Field(pattern=r"^(timeline|sample)$")
    source_id: str = Field(default="", max_length=100)
    name: str = Field(min_length=1, max_length=200)


class MCPTokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["read"], max_length=3)
    project_ids: list[str] = Field(default_factory=list, max_length=100)
    expires_in_s: float = Field(default=3600, ge=60, le=30 * 86400, allow_inf_nan=False)


def _feishu_send(url: str, text: str) -> bool:
    """飞书群机器人 webhook,fire-and-forget;失败静默(推送不阻塞业务)。"""
    import urllib.request

    try:
        req = urllib.request.Request(url, data=json.dumps(
            {"msg_type": "text", "content": {"text": text}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
            return r.status == 200
    except Exception:  # noqa: BLE001 —— 推送失败只影响通知,不影响告警入库
        return False


def _station_config_view(rt: StationRuntime, path: Path) -> dict[str, Any]:
    cfg = rt.cfg
    return {
        "meta": rt.meta(),
        "zones": [z.model_dump() for z in cfg.zones],
        "roi_states": [r.model_dump() for r in cfg.roi_states],
        "cycle": None if cfg.cycle is None else {"takt_s": cfg.cycle.takt_s, "start": cfg.cycle.start.describe(),
                                                 "end": cfg.cycle.end.describe(),
                                                 "grace_ms": cfg.cycle.grace_ms, "max_cycle_s": cfg.cycle.max_cycle_s},
        "steps": [{"id": s.id, "name": s.name, "preds": s.eff_preds, "optional": s.optional, "timeout_s": s.timeout_s,
                   "mode": s.evidence.mode, "conds": [c.describe() for c in s.evidence.conds]}
                  for s in rt.program.steps],
        "severity": rt.program.severity,
        "yaml": path.read_text(encoding="utf-8"),
    }


def create_app(manager: StationManager, hub: Hub, auth: AuthStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.loop = asyncio.get_running_loop()
        manager.start()
        task = asyncio.create_task(_push_states())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.to_thread(app.state.media.close)
            manager.stop()

    async def _push_states() -> None:
        while True:
            await asyncio.sleep(0.5)
            for sid, rt in manager.runtimes.items():
                if rt.state_cache:
                    hub.publish({"type": "state", "station": sid, "data": rt.state_cache})

    app = FastAPI(title="SOP Vision", lifespan=lifespan)
    app.state.manager = manager
    app.state.auth = auth
    app.state.media = MediaBridge.from_env()
    from .llm_provider import _default_env_path, load_env_file
    record_dir = os.getenv("MONIBUCA_RECORD_DIR", load_env_file(_default_env_path()).get("MONIBUCA_RECORD_DIR", ""))
    app.state.recordings = RecordingIndex(Path(record_dir)) if record_dir else None
    app.include_router(label_router(manager.labels, manager.label_jobs, manager.label_engines))
    from .notification_routes import router as notification_router
    app.include_router(notification_router(manager.notifications))
    from .audit import router as audit_router
    app.include_router(audit_router(manager.audit))
    from .model_routes import router as model_router
    app.include_router(model_router(manager.labels, manager.label_engines))
    from .training_routes import router as training_router
    app.include_router(training_router(manager.training))

    @app.exception_handler(LabelError)
    async def label_error(request: Request, e: LabelError):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    def current_user(request: Request) -> dict | None:
        """从 Cookie 或 Bearer 取会话;auth 未启用时返回伪 admin(本机工具模式)。"""
        h = request.headers.get("Authorization", "")
        if h.startswith("Bearer se_mcp_"):
            return auth.verify_mcp_token(h[7:]) if auth is not None else None
        if auth is None:
            return {"username": "local", "role": "admin"}
        token = request.cookies.get("vsession") or ""
        if h.startswith("Bearer "):
            token = h[7:]
        return auth.verify_session(token)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        user = current_user(request)
        request.state.user = user
        if request.headers.get("Authorization", "").startswith("Bearer se_mcp_"):
            from fastapi.responses import JSONResponse
            if user is None:
                return JSONResponse({"detail": "MCP令牌无效、已撤销或过期"}, status_code=401)
            try:
                check_mcp_read(request, user, manager.labels, manager.label_jobs)
            except (HTTPException, LabelError) as e:
                auth.audit_mcp(user["mcp_token_id"], request.method + " " + path, e.status_code if isinstance(e, HTTPException) else e.status)
                return JSONResponse({"detail": e.detail if isinstance(e, HTTPException) else str(e)},
                                    status_code=e.status_code if isinstance(e, HTTPException) else e.status)
            response = await call_next(request)
            auth.audit_mcp(user["mcp_token_id"], request.method + " " + path, response.status_code)
            return response
        if auth is not None and path.startswith("/api") \
                and path != "/api/health" and not path.startswith("/api/auth/"):
            try:
                preview_read = (request.method == "POST" and re.fullmatch(r"/api/stations/[^/]+/preview", path)) or \
                    (request.method == "DELETE" and re.fullmatch(r"/api/stations/[^/]+/preview/[\w-]+", path)) or \
                    (request.method == "POST" and re.fullmatch(r"/api/stations/[^/]+/preview/[\w-]+/lease", path))
                need = "admin" if path.startswith(("/api/users", "/api/mcp/tokens", "/api/mcp/audit", "/api/notifications", "/api/operations")) else \
                    ("viewer" if request.method == "GET" or preview_read else "operator")
                AuthStore.check(current_user(request), need)
            except AuthError as e:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": str(e)}, status_code=e.status)
        return await call_next(request)

    @app.middleware("http")
    async def audit_operations(request: Request, call_next):
        response = await call_next(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            # Registered templates exclude user IDs, tokens, query parameters and payloads.
            route = getattr(request.scope.get("route"), "path", "unmatched")
            actor = (getattr(request.state, "user", None) or {}).get("username", "anonymous")
            manager.audit.append(actor, request.method, route, response.status_code)
        return response

    @app.get("/api/mcp/scenes")
    def mcp_scenes(request: Request) -> list[dict]:
        allowed = (request.state.user or {}).get("project_ids", [])
        scenes = {manager.labels.get_project(pid)["scene_id"] for pid in allowed} if allowed else None
        # 不返回提示词、工位YAML、相机URI、远程服务或凭据。
        rows = [{"id": sid, "name": rt.cfg.name or sid, "kind": "station"}
                for sid, rt in manager.runtimes.items()]
        if manager.inspection is not None:
            rows += [{"id": s.id, "name": s.name, "kind": "inspection"} for s in manager.inspection.scenarios.values()]
        return [r for r in rows if scenes is None or r["id"] in scenes]

    def _mcp_auth() -> AuthStore:
        if auth is None:
            raise HTTPException(409, "先启用 --auth，再创建MCP专用令牌")
        return auth

    @app.post("/api/mcp/tokens", status_code=201)
    def mcp_token_add(body: MCPTokenBody, request: Request) -> dict:
        for pid in body.project_ids:
            manager.labels.get_project(pid)
        try:
            return _mcp_auth().issue_mcp_token(request.state.user["username"], body.name, body.scopes,
                                              body.project_ids, body.expires_in_s)
        except (AuthError, ValueError) as e:
            raise HTTPException(e.status if isinstance(e, AuthError) else 400, str(e)) from None

    @app.get("/api/mcp/tokens")
    def mcp_tokens() -> list[dict]:
        return _mcp_auth().list_mcp_tokens()

    @app.delete("/api/mcp/tokens/{tid}")
    def mcp_token_revoke(tid: str) -> dict:
        if not _mcp_auth().revoke_mcp_token(tid):
            raise HTTPException(404, "令牌不存在")
        return {"ok": True}

    @app.get("/api/mcp/audit")
    def mcp_audit(limit: int = Query(100, ge=1, le=500)) -> list[dict]:
        return _mcp_auth().mcp_audit(limit)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "stations": len(manager.runtimes), "auth": auth is not None}

    @app.get("/api/patrol/plans")
    def patrol_plans() -> list[dict[str, Any]]:
        return manager.patrol.plans()

    @app.post("/api/patrol/plans", status_code=201)
    def patrol_add(body: PatrolPlan) -> dict[str, Any]:
        rt = manager.get(body.station)
        if body.zone not in {z.id for z in rt.cfg.zones}:
            raise HTTPException(400, "区域不属于所选工位")
        return manager.patrol.add_plan(body)

    @app.delete("/api/patrol/plans/{pid}")
    def patrol_delete(pid: str) -> dict[str, Any]:
        if not manager.patrol.delete_plan(pid):
            raise HTTPException(404, "巡更计划不存在或已停用")
        return {"ok": True}

    @app.get("/api/patrol/report")
    def patrol_report(day: str | None = None) -> dict[str, Any]:
        from datetime import datetime
        try:
            return manager.patrol.report(day or datetime.now(LOCAL_TZ).date())
        except ValueError:
            raise HTTPException(400, "日期必须为 YYYY-MM-DD") from None

    # ---- 认证 / 用户(T5) ----
    @app.post("/api/auth/login")
    def auth_login(body: LoginBody) -> dict[str, Any]:
        if auth is None:
            raise HTTPException(400, "认证未启用(serve --auth 或 VISUAL_AUTH=1 开启)")
        try:
            token = auth.login(body.username, body.password)
        except AuthError as e:
            raise HTTPException(e.status, str(e)) from e
        resp = Response(json.dumps({"ok": True, "username": body.username}), media_type="application/json")
        resp.set_cookie("vsession", token, httponly=True, samesite="lax", max_age=12 * 3600, path="/")
        return resp

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> dict[str, Any]:
        if auth is not None:
            token = request.cookies.get("vsession", "")
            h = request.headers.get("Authorization", "")
            auth.logout(h[7:] if h.startswith("Bearer ") else token)
        resp = Response(json.dumps({"ok": True}), media_type="application/json")
        resp.delete_cookie("vsession", path="/")
        return resp

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict[str, Any]:
        u = current_user(request)
        if auth is not None and u is None:
            raise HTTPException(401, "未登录")
        return u or {"username": "local", "role": "admin"}

    @app.get("/api/users")
    def users_list(request: Request) -> list[dict[str, Any]]:
        if auth is None:
            raise HTTPException(400, "认证未启用")
        return auth.list_users()

    @app.post("/api/users")
    def users_add(body: UserBody) -> dict[str, Any]:
        if auth is None:
            raise HTTPException(400, "认证未启用")
        try:
            auth.set_user(body.username, body.password, body.role)
        except AuthError as e:
            raise HTTPException(e.status, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.delete("/api/users/{username}")
    def users_del(username: str) -> dict[str, Any]:
        if auth is None:
            raise HTTPException(400, "认证未启用")
        try:
            deleted = auth.delete_user(username)
        except AuthError as e:
            raise HTTPException(e.status, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not deleted:
            raise HTTPException(404, f"用户不存在:{username}")
        return {"ok": True}

    # ---- 告警中心(V7) ----
    @app.get("/api/alerts")
    def alerts_list(status: str | None = None, source: str | None = None,
                    limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        return manager.alerts.list(status=status, source=source, limit=limit)

    @app.get("/api/alerts/stats")
    def alerts_stats() -> dict[str, Any]:
        return manager.alerts.stats()

    @app.patch("/api/alerts/{aid}")
    def alerts_patch(aid: int, body: AlertPatchBody, request: Request) -> dict[str, Any]:
        try:
            rec = manager.alerts.update(aid, status=body.status, comment=body.comment,
                                        handler=current_user(request)["username"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if rec is None:
            raise HTTPException(404, f"告警不存在:{aid}")
        return rec

    @app.get("/api/stations")
    def stations() -> list[dict[str, Any]]:
        return [rt.state_cache or {"meta": rt.meta()} for rt in manager.runtimes.values()]

    @app.get("/api/stations/{sid}/state")
    def station_state(sid: str) -> dict[str, Any]:
        rt = manager.get(sid)
        return rt.state_cache or {"meta": rt.meta()}

    @app.get("/api/stations/{sid}/config")
    def station_config(sid: str) -> dict[str, Any]:
        rt = manager.get(sid)
        view = _station_config_view(rt, manager.paths[sid])
        available = bool(app.state.media.base and rt.cfg.source.preview_stream)
        view["preview"] = {"available": available,
                           "reason": "" if available else "未配置 Monibuca 预览，当前为带检测叠加的分析画面"}
        return view

    @app.post("/api/stations/{sid}/preview")
    async def preview_offer(sid: str, request: Request) -> Response:
        rt = manager.get(sid)
        if not rt.cfg.source.preview_stream:
            raise HTTPException(409, "工位未配置 source.preview_stream")
        if request.headers.get("content-type", "").split(";")[0] != "application/sdp":
            raise HTTPException(415, "预览信令需要 application/sdp")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 128 * 1024:
                raise HTTPException(413, "SDP 超过 128KB")
        if not body.lstrip().startswith(b"v=0"):
            raise HTTPException(400, "无效 SDP Offer")
        try:
            answer, token = await asyncio.to_thread(app.state.media.offer, rt.cfg.source.preview_stream,
                                                   bytes(body), sid, current_user(request)["username"])
        except MediaError as e:
            raise HTTPException(e.status, str(e)) from None
        if token and await request.is_disconnected():
            await asyncio.to_thread(app.state.media.release, token, sid, current_user(request)["username"])
            return Response(status_code=499)
        headers = {"Cache-Control": "no-store"}
        if token:
            headers["Location"] = f"/api/stations/{sid}/preview/{token}"
        return Response(answer, status_code=201, media_type="application/sdp", headers=headers)

    @app.delete("/api/stations/{sid}/preview/{token}", status_code=204)
    def preview_release(sid: str, token: str, request: Request) -> Response:
        manager.get(sid)
        try:
            app.state.media.release(token, sid, current_user(request)["username"])
        except MediaError as e:
            raise HTTPException(e.status, str(e)) from None
        return Response(status_code=204)

    @app.post("/api/stations/{sid}/preview/{token}/lease", status_code=204)
    def preview_lease(sid: str, token: str, request: Request) -> Response:
        manager.get(sid)
        try:
            app.state.media.renew(token, sid, current_user(request)["username"])
        except MediaError as e:
            raise HTTPException(e.status, str(e)) from None
        return Response(status_code=204)

    @app.get("/api/stations/{sid}/snapshot.jpg")
    def snapshot(sid: str, raw: bool = False) -> Response:
        data = manager.get(sid).jpeg(raw=raw)
        if data is None:
            raise HTTPException(503, "画面尚未就绪，请稍后重试")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    def recording_stream(sid: str) -> str:
        rt = manager.get(sid)
        if app.state.recordings is None:
            raise HTTPException(503, "未配置 MONIBUCA_RECORD_DIR，请将 Monibuca 录像目录配置为本机可读路径")
        if not rt.cfg.source.preview_stream:
            raise HTTPException(409, "工位未配置 source.preview_stream，无法绑定录像")
        stream = rt.cfg.source.preview_stream
        if any(other.cfg.source.preview_stream and other.cfg.source.preview_stream != stream and
               other.cfg.source.preview_stream.replace("/", "_") == stream.replace("/", "_")
               for other in manager.runtimes.values()):
            raise HTTPException(409, "录像文件名前缀与另一条流冲突，请调整流路径或使用独立录像目录")
        return stream

    @app.get("/api/stations/{sid}/recordings")
    def recordings_list(sid: str, start: str, end: str) -> dict[str, Any]:
        stream_path = recording_stream(sid)
        try:
            return app.state.recordings.list(stream_path, parse_time(start), parse_time(end))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/stations/{sid}/recordings/{name}")
    def recordings_video(sid: str, name: str) -> FileResponse:
        stream_path = recording_stream(sid)
        try:
            path = app.state.recordings.path(stream_path, name)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "录像不存在或不属于所选工位") from None
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})

    @app.get("/api/stations/{sid}/stream.mjpg")
    async def stream(sid: str, request: Request) -> StreamingResponse:
        rt = manager.get(sid)

        async def gen():
            last = -1
            while not await request.is_disconnected():
                if rt.frame_idx != last:
                    last = rt.frame_idx
                    data = rt.jpeg()
                    if data:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n" % (len(data), data)
                await asyncio.sleep(0.06)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/api/stations/{sid}/calibrate")
    def calibrate(sid: str, body: CalibrateBody) -> dict[str, Any]:
        rt = manager.get(sid)
        if rt.cfg.source.type == "sim":
            raise HTTPException(400, "仿真工位使用内置参考图，无需标定。真实工位请先把目标区域摆到参考状态再采集。")
        unknown = [z for z in (body.zones or []) if z not in rt.roi.trackers]
        if unknown:
            raise HTTPException(400, f"这些区域没有配置结果核验，无法标定：{', '.join(unknown)}")
        rt.request_calibration(body.zones)
        return {"ok": True, "zones": body.zones or list(rt.roi.trackers)}

    @app.post("/api/stations/{sid}/zones")
    def station_zones(sid: str, body: ZonesBody) -> dict[str, Any]:
        """区域编辑保存:先内存校验(含 roi_states 引用完整性),通过才写 YAML 并热重载。"""
        path = manager.paths.get(sid)
        if path is None:
            raise HTTPException(404, f"工位不存在:{sid}")
        zones = body.zones
        if not zones:
            raise HTTPException(400, "至少保留一个区域")
        ids = [str(z.get("id", "")) for z in zones]
        if len(set(ids)) != len(ids) or any(not SAFE_NAME.match(i) for i in ids):
            raise HTTPException(400, "区域 id 必须非空、不含特殊字符且不重复")
        cleaned = []
        for z in zones:
            poly = z.get("polygon")
            if not isinstance(poly, list) or len(poly) != 4:
                raise HTTPException(400, f"区域 {z.get('id')} 必须是 4 点矩形")
            try:
                pts = [[float(x), float(y)] for x, y in poly]
            except (TypeError, ValueError):
                raise HTTPException(400, f"区域 {z.get('id')} 坐标必须是数字") from None
            if any(not (0.0 <= v <= 1.0) for x, y in pts for v in (x, y)):
                raise HTTPException(400, f"区域 {z.get('id')} 坐标必须在 0~1 之间")
            cleaned.append({"id": str(z["id"]), "polygon": pts})
        import yaml as _yaml
        raw = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw["zones"] = cleaned
        try:
            StationConfig.model_validate(raw)
        except Exception as e:  # noqa: BLE001 —— 把校验错误原文给用户,比笼统 500 有用
            detail = str(e).splitlines()[0][:200]
            raise HTTPException(400, f"修改后的配置校验未通过(如 roi_states 引用了删除的区域),未保存:{detail}") from e
        rewrite_station_zones(path, cleaned)
        manager.reload(sid)
        return {"ok": True, "zones": len(cleaned)}

    @app.get("/api/cycles")
    def cycles(station: str | None = None, result: str | None = None, sn: str | None = None,
               limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return manager.store.list(limit=limit, result=result, sn=sn, station=station)

    @app.get("/api/cycles/{cid}")
    def cycle(cid: str) -> dict[str, Any]:
        rec = manager.store.get(cid)
        if rec is None:
            raise HTTPException(404, f"周期记录不存在：{cid}")
        return rec

    @app.get("/api/cycles/{cid}/frames/{name}")
    def cycle_frame(cid: str, name: str) -> FileResponse:
        if not SAFE_NAME.match(cid) or not SAFE_NAME.match(name):
            raise HTTPException(400, "非法的文件名")
        path = (manager.evidence_root / cid / "frames" / name).resolve()
        if manager.evidence_root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "证据图片不存在")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/stats")
    def stats(station: str | None = None, day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict[str, Any]:
        return manager.store.stats(station, day)

    @app.get("/api/trend")
    def trend(station: str | None = None, hours: int = Query(24, ge=1, le=168)) -> list[dict[str, Any]]:
        return manager.store.hourly(hours, station)

    # ---- 样本生成 ----
    @app.post("/api/samples/generate")
    async def samples_generate(ref: UploadFile = File(...), label: str = Form(...),
                               engine: str = Form("augment"), n: int = Form(10),
                               description: str = Form("与参考图同类场景"),
                               seed: int = Form(7)) -> dict[str, Any]:
        """上传参考图 → 批量生成带标注样本(标注继承);augment 秒级同步,cogview 每张 5~15s。"""
        from .inspection import MAX_IMAGES  # 复用同一个量级约束
        from .samplegen import COGVIEW_MAX_BATCH, generate_batch

        if engine not in ("augment", "cogview"):
            raise HTTPException(400, "engine 必须是 augment 或 cogview")
        if engine == "cogview" and n > COGVIEW_MAX_BATCH:
            raise HTTPException(400, f"生成式一次最多 {COGVIEW_MAX_BATCH} 张")
        if not 1 <= n <= 100:
            raise HTTPException(400, "n 必须在 1~100 之间")
        data = await ref.read()
        tmp = manager.data_dir / "samplegen" / f"ref-{int(time.time() * 1000)}-{SAFE_NAME.match(ref.filename) and ref.filename or 'ref.jpg'}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        try:
            rows = generate_batch(tmp, label.upper(), description, n, tmp.parent / "batch", engine=engine, seed=seed)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from e
        from .samplegen import write_manifest
        mp = write_manifest(tmp.parent / "batch", rows)
        return {"ok": True, "generated": sum(1 for r in rows if r.get("file")), "rows": rows,
                "manifest": str(mp.relative_to(manager.data_dir)).replace("\\", "/"),
                "manifest_hint": "标注继承自 label;生成式样本仅冷启动/调试用,不进验收集"}

    @app.get("/api/samples/manifest")
    def samples_manifest(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        """样本 manifest 倒序最近 N 行(跨批量合并)。"""
        mp = manager.data_dir / "samplegen" / "batch" / "samples.manifest.jsonl"
        if not mp.is_file():
            return []
        lines = mp.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for line in reversed(lines[-limit:]):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    @app.get("/api/samples/images/{name}")
    def samples_image(name: str) -> FileResponse:
        if not SAFE_NAME.match(name):
            raise HTTPException(400, "非法的文件名")
        path = (manager.data_dir / "samplegen" / "batch" / name).resolve()
        root = (manager.data_dir / "samplegen" / "batch").resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "样本图片不存在")
        return FileResponse(path, media_type="image/jpeg")

    # ---- 上线向导(V10) ----
    @app.post("/api/wizard/projects")
    def wizard_create(body: dict[str, str]) -> dict[str, Any]:
        from .wizard import WizardStore, skeleton_yaml
        name = str(body.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "缺少工位名称")
        store = WizardStore(manager.data_dir / "wizard.db")
        rec = store.create(name, skeleton_yaml(f"WZ-{int(time.time() * 1000) % 100000}", name))
        store.close()
        return rec

    @app.get("/api/wizard/projects")
    def wizard_list() -> list[dict[str, Any]]:
        from .wizard import WizardStore
        store = WizardStore(manager.data_dir / "wizard.db")
        rows = store.list()
        store.close()
        return rows

    @app.get("/api/wizard/projects/{pid}")
    def wizard_get(pid: str) -> dict[str, Any]:
        from .wizard import WizardStore
        store = WizardStore(manager.data_dir / "wizard.db")
        rec = store.get(pid)
        store.close()
        if rec is None:
            raise HTTPException(404, f"项目不存在:{pid}")
        return rec

    @app.post("/api/wizard/projects/{pid}/compile")
    def wizard_compile(pid: str, body: CompileBody) -> dict[str, Any]:
        from .wizard import WizardStore, compile_steps
        if manager.inspection is None:
            raise HTTPException(503, "文档编译需要大模型凭据(.env 配置 ZHIPU_API_KEY)")
        store = WizardStore(manager.data_dir / "wizard.db")
        if store.get(pid) is None:
            store.close()
            raise HTTPException(404, f"项目不存在:{pid}")
        out = compile_steps(manager.inspection.provider, body.text)
        store.close()
        return out

    @app.post("/api/wizard/projects/{pid}/steps")
    def wizard_steps(pid: str, body: StepsBody) -> dict[str, Any]:
        from .wizard import WizardStore, apply_steps
        store = WizardStore(manager.data_dir / "wizard.db")
        rec = store.get(pid)
        if rec is None:
            store.close()
            raise HTTPException(404, f"项目不存在:{pid}")
        if not body.steps:
            store.close()
            raise HTTPException(400, "至少一个步骤")
        rec = store.update(pid, yaml_text=apply_steps(rec["yaml_text"], body.steps), status="verified")
        store.close()
        return rec

    @app.post("/api/wizard/projects/{pid}/source")
    def wizard_source(pid: str, body: SourceBody) -> dict[str, Any]:
        """Step2 画面接入:填相机地址(sim 用于演示);部署前必须给出有效地址。"""
        from .wizard import WizardStore
        store = WizardStore(manager.data_dir / "wizard.db")
        rec = store.get(pid)
        if rec is None:
            store.close()
            raise HTTPException(404, f"项目不存在:{pid}")
        import yaml as _yaml
        raw = _yaml.safe_load(rec["yaml_text"]) or {}
        raw["source"] = {"type": body.type, "uri": body.uri, "fps": 25, "width": 1280, "height": 720}
        rec = store.update(pid, yaml_text=_yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        store.close()
        return rec

    @app.post("/api/wizard/projects/{pid}/deploy")
    def wizard_deploy(pid: str) -> dict[str, Any]:
        from .wizard import WizardStore
        store = WizardStore(manager.data_dir / "wizard.db")
        rec = store.get(pid)
        if rec is None:
            store.close()
            raise HTTPException(404, f"项目不存在:{pid}")
        if rec["status"] != "verified":
            store.close()
            raise HTTPException(409, "先确认步骤(状态 verified)再部署")
        dest = manager.data_dir / "wizard" / f"{pid}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rec["yaml_text"], encoding="utf-8")
        try:
            sid = manager.add_station(dest)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e  # 无效 RTSP 等:fail-fast,不产生僵尸源
        store.update(pid, status="live")
        store.close()
        return {"ok": True, "station": sid, "yaml": str(dest)}

    # ---- GIS 定位 ----
    @app.get("/api/geo")
    def geo_list(kind: str | None = None) -> list[dict[str, Any]]:
        return manager.geo.list(kind)

    @app.get("/api/geo/map")
    def geo_map() -> dict[str, Any]:
        """地图页一次拿全:点位 + 各点实时状态与最近告警数(前端打点着色)。"""
        points = manager.geo.list()
        out = []
        for p in points:
            rt = manager.runtimes.get(p["id"])
            out.append({**p, "status": "online" if rt is not None else "unknown",
                        "pending_alerts": len([a for a in manager.alerts.list(limit=200)
                                               if a["station"] == p["id"] and a["status"] == "pending"])})
        return {"points": out, "center": None}


    @app.post("/api/geo")
    def geo_upsert(body: GeoBody) -> dict[str, Any]:
        try:
            return manager.geo.upsert(body.id, body.name, body.kind, body.lat, body.lng)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/geo/{pid}")
    def geo_delete(pid: str) -> dict[str, Any]:
        if not manager.geo.delete(pid):
            raise HTTPException(404, f"点位不存在:{pid}")
        return {"ok": True}

    # ---- 相机接入:ONVIF / GB28181 ----
    @app.get("/api/cameras/onvif/status")
    def onvif_status() -> dict[str, Any]:
        import importlib.util
        return {"wsdiscovery": importlib.util.find_spec("wsdiscovery") is not None,
                "onvif_zeep_async": importlib.util.find_spec("onvif") is not None}

    @app.post("/api/cameras/onvif/discover")
    def onvif_discover(body: OnvifDiscoverBody) -> dict[str, Any]:
        from .onvif import wsdiscovery_probe
        try:
            return {"ok": True, "devices": wsdiscovery_probe(body.timeout_s)}
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/cameras/onvif/adopt")
    def onvif_adopt(body: OnvifAdoptBody) -> dict[str, Any]:
        """读相机 RTSP 主码流地址与 GeoLocation(若有);返回信息供向导/工位 source 使用。"""
        from .onvif import device_rtsp_and_geo
        try:
            return {"ok": True, **device_rtsp_and_geo(body.host, body.port, body.user, body.password)}
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/cameras/gb28181/status")
    def gb28181_status() -> dict[str, Any]:
        env = {**load_env_file(_default_env_path()), **os.environ}
        return {"configured": bool(env.get("GB28181_WVP_URL")), "hint": "配置 GB28181_WVP_URL/USER/PASS 后启用;部署侧为 wvp-pro + ZLMediaKit"}

    @app.post("/api/cameras/gb28181/sync")
    def gb28181_sync() -> dict[str, Any]:
        """从 wvp-pro 拉设备目录(含国标必选经纬度)→ GIS 点位库。"""
        env = {**load_env_file(_default_env_path()), **os.environ}
        if not env.get("GB28181_WVP_URL"):
            raise HTTPException(400, "未配置 GB28181_WVP_URL(部署 wvp-pro + ZLMediaKit 后填写)")
        from .gb28181 import WvpClient
        from .geo import sync_from_device_list
        c = WvpClient(env["GB28181_WVP_URL"], env.get("GB28181_USER", "admin"), env.get("GB28181_PASS", ""))
        try:
            devs = c.devices()
        except Exception:
            raise HTTPException(502, "WVP 目录读取失败，请检查服务、版本、会话和权限") from None
        synced = sync_from_device_list(devs, manager.geo)
        return {"ok": True, "devices": len(devs), "synced": len(synced)}

    # ---- 厂区平面图(轻量 GIS) ----
    @app.post("/api/plans", status_code=201)
    async def plans_create(name: str = Form(...), image: UploadFile = File(...)) -> dict[str, Any]:
        from .plans import ALLOWED_IMG, PlanStore
        ext = Path(image.filename or "").suffix.lower()
        if ext not in ALLOWED_IMG:
            raise HTTPException(400, f"仅支持图片 {'/'.join(sorted(ALLOWED_IMG))}(CAD 请先导出图片)")
        data = await image.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, "图片超过 20MB")
        if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
            raise HTTPException(400, "上传内容不是可解码的图片")
        pdir = manager.data_dir / "plans"
        pdir.mkdir(parents=True, exist_ok=True)
        safe = f"{uuid.uuid4().hex}{ext}"
        (pdir / safe).write_bytes(data)
        store = PlanStore(manager.data_dir / "plans.db")
        rec = store.create(name, safe)
        store.close()
        return rec

    @app.get("/api/plans")
    def plans_list() -> list[dict[str, Any]]:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        rows = store.list()
        store.close()
        return rows

    @app.get("/api/plans/{pid}")
    def plans_get(pid: str) -> dict[str, Any]:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        rec = store.get(pid)
        pts = store.points(pid)
        store.close()
        if rec is None:
            raise HTTPException(404, f"平面图不存在:{pid}")
        return {**rec, "points": pts}

    @app.get("/api/plans/{pid}/image")
    def plans_image(pid: str) -> FileResponse:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        rec = store.get(pid)
        store.close()
        if rec is None:
            raise HTTPException(404, "平面图不存在")
        path = (manager.data_dir / "plans" / rec["image"]).resolve()
        root = (manager.data_dir / "plans").resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "底图不存在")
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media)

    @app.delete("/api/plans/{pid}")
    def plans_delete(pid: str) -> dict[str, Any]:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        ok = store.delete(pid)
        store.close()
        if not ok:
            raise HTTPException(404, f"平面图不存在:{pid}")
        return {"ok": True}

    @app.post("/api/plans/{pid}/points", status_code=201)
    def plans_add_point(pid: str, body: PlanPointBody) -> dict[str, Any]:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        if store.get(pid) is None:
            store.close()
            raise HTTPException(404, f"平面图不存在:{pid}")
        try:
            rec = store.add_point(pid, body.name, body.kind, body.x, body.y, body.station_id)
        except ValueError as e:
            store.close()
            raise HTTPException(400, str(e)) from e
        store.close()
        return rec

    @app.delete("/api/plans/{pid}/points/{ptid}")
    def plans_del_point(pid: str, ptid: str) -> dict[str, Any]:
        from .plans import PlanStore
        store = PlanStore(manager.data_dir / "plans.db")
        ok = store.delete_point(ptid, pid)
        store.close()
        if not ok:
            raise HTTPException(404, "点位不存在")
        return {"ok": True}

    # ---- 视频时间轴标注(V11) ----
    @app.post("/api/timelines", status_code=201)
    async def timeline_create(name: str = Form(...), video: UploadFile = File(...)) -> dict[str, Any]:
        from .timeline import TimelineStore, probe_video
        data = await video.read()
        vid_dir = manager.data_dir / "timelines"
        vid_dir.mkdir(parents=True, exist_ok=True)
        safe = f"{int(time.time() * 1000)}-{(SAFE_NAME.match(video.filename) and video.filename) or 'video.mp4'}"
        vpath = vid_dir / safe
        vpath.write_bytes(data)
        try:
            duration, fps = probe_video(vpath)
        except ValueError as e:
            vpath.unlink(missing_ok=True)
            raise HTTPException(400, str(e)) from e
        store = TimelineStore(manager.data_dir / "timelines.db")
        rec = store.create(name, safe, duration, fps)
        store.close()
        return rec

    @app.get("/api/timelines")
    def timeline_list() -> list[dict[str, Any]]:
        from .timeline import TimelineStore
        store = TimelineStore(manager.data_dir / "timelines.db")
        rows = store.list()
        store.close()
        return rows

    @app.get("/api/timelines/{tid}")
    def timeline_get(tid: str) -> dict[str, Any]:
        from .timeline import TimelineStore
        store = TimelineStore(manager.data_dir / "timelines.db")
        rec = store.get(tid)
        store.close()
        if rec is None:
            raise HTTPException(404, f"标注项目不存在:{tid}")
        return rec

    @app.post("/api/timelines/{tid}/intervals")
    def timeline_intervals(tid: str, body: IntervalsBody) -> dict[str, Any]:
        """保存区间标注;extract=True 时校验+抽帧,产出回归 manifest 行。"""
        from .timeline import TimelineStore, check_intervals, extract_interval_frames, write_manifest
        store = TimelineStore(manager.data_dir / "timelines.db")
        rec = store.get(tid)
        if rec is None:
            store.close()
            raise HTTPException(404, f"标注项目不存在:{tid}")
        check = check_intervals(body.intervals, float(rec["duration_s"]))
        if not check["ok"]:
            store.close()
            raise HTTPException(400, ";".join(check["problems"][:3]))
        rec = store.update(tid, intervals=body.intervals, status="annotated")
        extracted = 0
        if body.extract:
            vpath = manager.data_dir / "timelines" / rec["video"]
            frames_dir = manager.data_dir / "timelines" / tid / "frames"
            rows = extract_interval_frames(vpath, body.intervals, frames_dir, body.per_interval)
            write_manifest(frames_dir, rows)
            extracted = len(rows)
        store.close()
        return {**rec, "check": check, "extracted": extracted,
                "frames_url_prefix": f"/api/timelines/{tid}/frames"}

    @app.post("/api/label-projects/{pid}/import-evidence", status_code=201)
    def label_import_evidence(pid: str, body: LabelEvidenceBody) -> dict:
        """仅导入登记的内部图像。来源步骤/整图标签不转换为目标框真值。"""
        from .labels import MAX_IMAGE_BYTES
        manager.labels.get_project(pid)
        if not SAFE_NAME.fullmatch(body.name) or Path(body.name).suffix.lower() not in (".jpg", ".jpeg", ".png"):
            raise HTTPException(400, "需要内部资源的图像名称")
        if body.kind == "timeline":
            from .timeline import TimelineStore
            table = TimelineStore(manager.data_dir / "timelines.db")
            try:
                record = table.get(body.source_id)
            finally:
                table.close()
            if not record:
                raise HTTPException(404, "时间轴项目不存在")
            base = manager.data_dir / "timelines"
            folder = base / record["id"] / "frames"
            group = "timeline:" + record["video"]
        else:
            base = manager.data_dir / "samplegen"
            folder = base / "batch"
            group = "samplegen-batch"
        path = (folder / body.name).resolve()
        if not path.is_relative_to(base.resolve()) or path.parent != folder.resolve() or not path.is_file():
            raise HTTPException(404, "图像不存在")
        with path.open("rb") as stream:
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        return manager.labels.add_asset(pid, raw, body.name, {"source_kind": body.kind, "source_ref": body.name,
            "source_id": body.source_id, "synthetic": body.kind == "sample", "group_id": group})

    @app.get("/api/timelines/{tid}/video")
    def timeline_video(tid: str) -> FileResponse:
        from .timeline import TimelineStore
        store = TimelineStore(manager.data_dir / "timelines.db")
        rec = store.get(tid)
        store.close()
        if rec is None:
            raise HTTPException(404, "标注项目不存在")
        path = (manager.data_dir / "timelines" / rec["video"]).resolve()
        root = (manager.data_dir / "timelines").resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "视频不存在")
        return FileResponse(path, media_type="video/mp4")

    # ---- 影子真值(V7 回流) ----
    @app.get("/api/shadow/samples")
    def shadow_samples(unlabeled: int = Query(1), limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        from .shadow import ShadowStore
        store = ShadowStore(manager.data_dir / "shadow.db")
        try:
            return store.list(bool(unlabeled), limit)
        finally:
            store.close()

    @app.get("/api/shadow/report")
    def shadow_report() -> dict[str, Any]:
        store = ShadowStore(manager.data_dir / "shadow.db")
        try:
            return store.report()
        finally:
            store.close()

    @app.get("/api/shadow/{sample_id}")
    def shadow_sample(sample_id: int) -> dict[str, Any]:
        store = ShadowStore(manager.data_dir / "shadow.db")
        try:
            row = store.get(sample_id)
            if row is None:
                raise HTTPException(404, "影子样本不存在")
            return row
        finally:
            store.close()

    @app.post("/api/shadow/{sid}/truth")
    def shadow_truth(sid: int, body: TruthBody) -> dict[str, Any]:
        from .shadow import ShadowStore
        if body.truth not in ("PASS", "NG"):
            raise HTTPException(400, "truth 必须是 PASS 或 NG")
        store = ShadowStore(manager.data_dir / "shadow.db")
        try:
            if not store.set_truth(sid, body.truth):
                raise HTTPException(404, "影子样本不存在")
        finally:
            store.close()
        return {"ok": True}

    @app.get("/api/timelines/{tid}/frames/{name}")
    def timeline_frame(tid: str, name: str) -> FileResponse:
        if not SAFE_NAME.match(tid) or not SAFE_NAME.match(name):
            raise HTTPException(400, "非法的文件名")
        path = (manager.data_dir / "timelines" / tid / "frames" / name).resolve()
        root = (manager.data_dir / "timelines" / tid / "frames").resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "抽帧不存在")
        return FileResponse(path, media_type="image/jpeg")

    # ---- 免训练外观检测(AnomalyDINO 式) ----
    @app.post("/api/anomaly/{scene}/register", status_code=201)
    async def anomaly_register(scene: str, files: list[UploadFile] = File(...),
                               threshold: float = Form(0.12)) -> dict[str, Any]:
        """登记 OK 图:上传 1-8 张好品图建立外观记忆库(免训练免标注坏品)。"""
        from .anomaly import AnomalyEngine
        if not 1 <= len(files) <= 8:
            raise HTTPException(400, "OK 图 1~8 张")
        if not 0.02 <= threshold <= 1.0:
            raise HTTPException(400, "threshold 需在 0.02~1.0")
        d = manager.data_dir / "anomaly" / scene
        eng = AnomalyEngine.load(d) if (d / "engine.json").is_file() else AnomalyEngine(threshold)
        added = 0
        for f in files:
            data = await f.read()
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if arr is None:
                raise HTTPException(400, f"{f.filename} 不是可解码图片")
            eng.add_ok(arr)
            added += 1
        eng.save(d)
        return {"ok": True, "scene": scene, "total_refs": eng.n_refs, "added": added}

    @app.post("/api/anomaly/{scene}/inspect", status_code=201)
    async def anomaly_inspect(scene: str, image: UploadFile = File(...)) -> dict[str, Any]:
        """判定一张图;结果同步进影子库(供真值标注与漏检/误杀统计)。"""
        from .anomaly import AnomalyEngine
        d = manager.data_dir / "anomaly" / scene
        if not (d / "engine.json").is_file():
            raise HTTPException(409, "该场景尚未登记 OK 图,无法判定")
        eng = AnomalyEngine.load(d)
        data = await image.read()
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise HTTPException(400, "图片不可解码")
        out = eng.inspect(arr)
        rec, _ = manager.alerts.create(scene, "anomaly", out["verdict"] or "UNCERTAIN",
                                       "warn", f"外观异常分 {out['score']}(阈值 {out['threshold']})",
                                       ref_id=f"{int(time.time() * 1000)}")
        if manager.shadow_store is not None:
            manager.shadow_store.add("anomaly", scene, str(rec["id"]), out["verdict"], out["score"],
                                     {"score": out["score"], "max_patch": out["max_patch"]})
        return {"ok": True, "scene": scene, "alert_id": rec["id"], **out}

    # ---- 模板库 / 外部信号 ----
    @app.get("/api/templates")
    def templates() -> dict[str, Any]:
        return list_templates()

    @app.get("/api/templates/station/{tid}")
    def station_template(tid: str, request: Request) -> dict[str, Any]:
        try:
            text = render_station(tid, dict(request.query_params))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        return {"id": tid, "yaml": text}

    @app.post("/api/stations/{sid}/signal", status_code=202)
    def station_signal(sid: str, body: SignalBody) -> dict[str, Any]:
        """兼容人工 HTTP 信号：只入队，不从请求线程推进引擎。"""
        rt = manager.get(sid)
        try:
            return {"ok": True, **rt.signal_inbox.push_legacy(body.name, body.value)}
        except SignalError as e:
            raise HTTPException(e.status, {"code": e.code, "message": str(e)}) from None

    @app.post("/api/stations/{sid}/signals", status_code=202)
    def station_signals(sid: str, body: SignalPacket) -> dict[str, Any]:
        rt = manager.get(sid)
        if body.source not in rt.cfg.signal_sources:
            raise HTTPException(403, "信号来源未在工位 signal_sources 中注册")
        try:
            return rt.signal_inbox.push(body)
        except SignalError as e:
            raise HTTPException(e.status, {"code": e.code, "message": str(e)}) from None

    @app.get("/api/stations/{sid}/signals/audit")
    def station_signal_audit(sid: str, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        manager.get(sid)
        return manager.signal_store.audit(sid, limit)

    # ---- 拍照即检(V 系列) ----
    def _inspection() -> InspectionService:
        if manager.inspection is None:
            raise HTTPException(503, "拍照即检未启用:缺少 ZHIPU_API_KEY(在 sop-vision/.env 配置后重启服务)")
        return manager.inspection

    @app.get("/api/inspection/scenarios")
    def inspection_scenarios() -> list[dict[str, Any]]:
        ins = _inspection()
        return [{"id": s.id, "name": s.name, "enabled": s.enabled, "mode": s.mode,
                 "ok_refs": len(s.ok_refs), "ng_refs": len(s.ng_refs)}
                for s in ins.scenarios.values()]

    @app.post("/api/inspection/tasks", status_code=202)
    async def inspection_submit(scenario_id: str = Form(...), files: list[UploadFile] = File(...)) -> dict[str, Any]:
        ins = _inspection()
        payloads = [await f.read() for f in files]
        try:
            rec = ins.submit(scenario_id, payloads)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except PermissionError as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return ins.task_view(rec)

    @app.get("/api/inspection/tasks")
    def inspection_tasks(status: str | None = None, scenario: str | None = None,
                         limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        ins = _inspection()
        return [ins.task_view(r) for r in ins.store.list(limit=limit, status=status, scenario=scenario)]

    @app.get("/api/inspection/stats")
    def inspection_stats() -> dict[str, Any]:
        return _inspection().store.stats()

    @app.get("/api/inspection/tasks/{tid}")
    def inspection_task(tid: str) -> dict[str, Any]:
        rec = _inspection().store.get(tid)
        if rec is None:
            raise HTTPException(404, f"任务不存在:{tid}")
        return _inspection().task_view(rec)

    @app.get("/api/inspection/tasks/{tid}/images/{name}")
    def inspection_image(tid: str, name: str) -> FileResponse:
        if not SAFE_NAME.match(tid) or not SAFE_NAME.match(name):
            raise HTTPException(400, "非法的文件名")
        ins = _inspection()
        path = (ins.images_root / tid / name).resolve()
        if ins.images_root.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "图片不存在")
        return FileResponse(path, media_type="image/jpeg")

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        if auth is not None:  # 流量含画面与数据,未认证直接拒绝
            token = sock.query_params.get("token") or sock.cookies.get("vsession", "")
            if auth.verify_session(token) is None:
                await sock.close(code=4401)
                return
        await sock.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        hub.clients.add(q)
        try:
            await sock.send_json({"type": "hello", "stations": [rt.meta() for rt in manager.runtimes.values()]})
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(q)

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def build_app(config_paths: list[Path], data_dir: Path, inspection_config: Path | None = None,
              vlm_transport=None, provider_cfg: ProviderConfig | None = None,
              auth: AuthStore | None = None, alert_store: AlertStore | None = None,
              shadow: bool = False) -> FastAPI:
    hub = Hub()
    return create_app(StationManager(config_paths, data_dir, hub, inspection_config, vlm_transport,
                                     provider_cfg, alert_store, shadow), hub, auth)
