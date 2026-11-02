"""HTTP / WebSocket 服务：REST 查询、MJPEG 视频流、证据图片、实时事件推送，并托管 web/ 静态界面。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .alerts import AlertStore
from .auth import AuthError, AuthStore
from .dsl import StationConfig, load_config
from .evidence import CycleStore
from .events import SIGNAL, Event
from .inspection import InspectionService, InspectionStore, load_scenarios
from .llm_provider import ProviderConfig, VLMProvider
from .runtime import StationRuntime
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
                 alert_store: AlertStore | None = None):
        self.data_dir = data_dir
        self.hub = hub
        self.store = CycleStore(data_dir / "sop.db")
        self.alerts = alert_store or AlertStore(data_dir / "alerts.db")
        self.feishu_webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()
        self.evidence_root = data_dir / "evidence"
        self.runtimes: dict[str, StationRuntime] = {}
        self.paths: dict[str, Path] = {}
        self.threads: list[threading.Thread] = []
        self._threads: dict[str, threading.Thread] = {}
        # 拍照即检:给了配置且有凭据才启用;没凭据时端点返回明确指引,而不是悄悄坏掉
        self.inspection: InspectionService | None = None
        if inspection_config is not None:
            cfg = provider_cfg or ProviderConfig.from_env()
            if cfg.api_key:
                self.inspection = InspectionService(
                    load_scenarios(inspection_config), InspectionStore(data_dir / "sop.db"),
                    data_dir / "inspection", VLMProvider(cfg, vlm_transport),
                    publish=self._on_inspection_msg, refs_root=data_dir)
            else:
                print("提示:未配置 ZHIPU_API_KEY(.env),拍照即检功能不可用,其余功能不受影响")
        for p in config_paths:
            cfg: StationConfig = load_config(p)
            if cfg.station in self.runtimes:
                raise ValueError(f"工位 id 重复: {cfg.station}")
            rt = StationRuntime(cfg, data_dir, base_dir=ROOT, store=self.store)
            rt.listeners.append(self._forwarder(cfg.station))
            self.runtimes[cfg.station] = rt
            self.paths[cfg.station] = p

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
        """拍照即检事件:推送 WS;NG/失败结论入告警中心。"""
        self.hub.publish(msg)
        d = msg.get("data", {})
        if msg.get("kind") == "inspection" and d.get("status") == "done" and d.get("verdict") == "NG":
            rec, new = self.alerts.create(d.get("scenario", "inspection"), "inspection", "NG",
                                          "warn", f"拍照即检判 NG:{d.get('reason', '')}", ref_id=d.get("id"))
            if new:
                self._push_out(rec)

    def _push_out(self, rec: dict[str, Any]) -> None:
        """告警广播 + 飞书推送(新建时调用一次;推送后回写标记防重)。"""
        self.hub.publish({"type": "alert", "data": rec})
        if self.feishu_webhook and not rec.get("pushed"):
            def send():
                if _feishu_send(self.feishu_webhook,
                                f"[视觉告警] {rec['station']} {rec['kind']}\n{rec['message']}\n状态: 待处理"):
                    self.alerts.mark_pushed(rec["id"])
            threading.Thread(target=send, name="feishu", daemon=True).start()

    def start(self) -> None:
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
        cfg: StationConfig = load_config(self.paths[sid])
        if cfg.station != sid:
            raise HTTPException(400, f"配置中的 station id({cfg.station})与工位({sid})不一致,不能重载")
        rt = StationRuntime(cfg, self.data_dir, base_dir=ROOT, store=self.store)
        rt.listeners.append(self._forwarder(sid))
        self.runtimes[sid] = rt
        nt = threading.Thread(target=rt.run, name=f"station-{sid}", daemon=True)
        nt.start()
        self.threads.append(nt)
        self._threads[sid] = nt
        self.hub.publish({"type": "event", "station": sid, "kind": "reloaded", "data": {"ok": True}})

    def stop(self) -> None:
        for rt in self.runtimes.values():
            rt.stop()
        for t in self.threads:
            t.join(timeout=3)
        if self.inspection is not None:
            self.inspection.stop()
            self.inspection.store.close()
        self.store.close()

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
        yield
        task.cancel()
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

    def current_user(request: Request) -> dict | None:
        """从 Cookie 或 Bearer 取会话;auth 未启用时返回伪 admin(本机工具模式)。"""
        if auth is None:
            return {"username": "local", "role": "admin"}
        token = request.cookies.get("vsession") or ""
        h = request.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            token = h[7:]
        return auth.verify_session(token)

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path = request.url.path
        if auth is not None and path.startswith("/api") \
                and path != "/api/health" and not path.startswith("/api/auth/"):
            try:
                need = "admin" if path.startswith("/api/users") else \
                    ("viewer" if request.method == "GET" else "operator")
                AuthStore.check(current_user(request), need)
            except AuthError as e:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": str(e)}, status_code=e.status)
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "stations": len(manager.runtimes), "auth": auth is not None}

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
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.delete("/api/users/{username}")
    def users_del(username: str) -> dict[str, Any]:
        if auth is None:
            raise HTTPException(400, "认证未启用")
        if not auth.delete_user(username):
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
        return _station_config_view(manager.get(sid), manager.paths[sid])

    @app.get("/api/stations/{sid}/snapshot.jpg")
    def snapshot(sid: str, raw: bool = False) -> Response:
        data = manager.get(sid).jpeg(raw=raw)
        if data is None:
            raise HTTPException(503, "画面尚未就绪，请稍后重试")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

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

    @app.post("/api/stations/{sid}/signal")
    def station_signal(sid: str, body: SignalBody) -> dict[str, Any]:
        """外部信号统一入口:扫码枪/扭矩枪/PLC/声学事件脚本都从这里进引擎。"""
        rt = manager.get(sid)
        rt.engine.process(Event(time.time(), SIGNAL, name=body.name, value=body.value))
        return {"ok": True}

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
              auth: AuthStore | None = None, alert_store: AlertStore | None = None) -> FastAPI:
    hub = Hub()
    return create_app(StationManager(config_paths, data_dir, hub, inspection_config, vlm_transport,
                                     provider_cfg, alert_store), hub, auth)
