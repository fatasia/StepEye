"""HTTP / WebSocket 服务：REST 查询、MJPEG 视频流、证据图片、实时事件推送，并托管 web/ 静态界面。"""
from __future__ import annotations

import asyncio
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .dsl import StationConfig, load_config
from .evidence import CycleStore
from .inspection import InspectionService, InspectionStore, load_scenarios
from .llm_provider import ProviderConfig, VLMProvider
from .runtime import StationRuntime

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
SAFE_NAME = re.compile(r"^[\w.\-]+$")
PUSH_KINDS = {"cycle_start", "step_done", "violation", "cycle_end", "quality", "calibrated"}


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


class StationManager:
    def __init__(self, config_paths: list[Path], data_dir: Path, hub: Hub,
                 inspection_config: Path | None = None, vlm_transport=None,
                 provider_cfg: ProviderConfig | None = None):
        self.data_dir = data_dir
        self.hub = hub
        self.store = CycleStore(data_dir / "sop.db")
        self.evidence_root = data_dir / "evidence"
        self.runtimes: dict[str, StationRuntime] = {}
        self.paths: dict[str, Path] = {}
        self.threads: list[threading.Thread] = []
        # 拍照即检:给了配置且有凭据才启用;没凭据时端点返回明确指引,而不是悄悄坏掉
        self.inspection: InspectionService | None = None
        if inspection_config is not None:
            cfg = provider_cfg or ProviderConfig.from_env()
            if cfg.api_key:
                self.inspection = InspectionService(
                    load_scenarios(inspection_config), InspectionStore(data_dir / "sop.db"),
                    data_dir / "inspection", VLMProvider(cfg, vlm_transport),
                    publish=self.hub.publish, refs_root=data_dir)
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
        return fwd

    def start(self) -> None:
        if self.inspection is not None:
            self.inspection.start()
        for sid, rt in self.runtimes.items():
            t = threading.Thread(target=rt.run, name=f"station-{sid}", daemon=True)
            t.start()
            self.threads.append(t)

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


def _station_config_view(rt: StationRuntime, path: Path) -> dict[str, Any]:
    cfg = rt.cfg
    return {
        "meta": rt.meta(),
        "zones": [z.model_dump() for z in cfg.zones],
        "roi_states": [r.model_dump() for r in cfg.roi_states],
        "cycle": {"takt_s": cfg.cycle.takt_s, "start": cfg.cycle.start.describe(), "end": cfg.cycle.end.describe(),
                  "grace_ms": cfg.cycle.grace_ms, "max_cycle_s": cfg.cycle.max_cycle_s},
        "steps": [{"id": s.id, "name": s.name, "preds": s.eff_preds, "optional": s.optional, "timeout_s": s.timeout_s,
                   "mode": s.evidence.mode, "conds": [c.describe() for c in s.evidence.conds]}
                  for s in rt.program.steps],
        "severity": rt.program.severity,
        "yaml": path.read_text(encoding="utf-8"),
    }


def create_app(manager: StationManager, hub: Hub) -> FastAPI:
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

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "stations": len(manager.runtimes)}

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
              vlm_transport=None, provider_cfg: ProviderConfig | None = None) -> FastAPI:
    hub = Hub()
    return create_app(StationManager(config_paths, data_dir, hub, inspection_config, vlm_transport,
                                     provider_cfg), hub)
