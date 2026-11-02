"""光照突变算法与模板/信号端点的离线测试。"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from visual.dsl import QualityCfg
from visual.engine import Engine
from visual.events import SIGNAL, Event
from visual.perception.base import Context, build_geoms
from visual.perception.quality import LIGHT_ADAPT_AFTER, CONFIRM, QualityPerceiver
from visual.server import build_app
from visual.templates import list_templates, render_station


def _frame(mean: float, seed: int = 0) -> np.ndarray:
    """有纹理的合成 BGR 帧(Laplacian 方差足够,不触发 blur),整体亮度可控。"""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 12, (240, 320)).astype(np.float32)
    base = np.clip(np.full((240, 320), mean, np.float32) + noise, 0, 255).astype(np.uint8)
    return np.dstack([base, base, base])  # 灰度值复制成 3 通道,喂 BGR 管线


def _cfg() -> QualityCfg:
    return QualityCfg(enabled=True, blur_min=2.0, brightness=(0, 255), lighting_drift=0.3, every_n=1)


def test_lighting_drift_degrades_then_recovers():
    q = QualityPerceiver(_cfg())
    ctx = Context({})
    events = []
    for i in range(CONFIRM):  # 稳定基準 100 亮度
        events += q.process(_frame(100, i), 0.1 * i, ctx)
    assert events == [] and q.degraded is False
    for i in range(CONFIRM):  # 亮度跳到 170(漂移 70%)
        events += q.process(_frame(170, 10 + i), 1.0 + 0.1 * i, ctx)
    assert q.degraded and q.reason == "lighting_drift"
    assert events[-1].state == "degraded" and events[-1].value == "lighting_drift"
    # 持续劣化 LIGHT_ADAPT_AFTER 次确认 → 自适应新光照 → 恢复
    n = CONFIRM
    recovered = False
    for i in range(LIGHT_ADAPT_AFTER + CONFIRM + 5):
        evs = q.process(_frame(170, 30 + i), 2.0 + 0.1 * i, ctx)
        n += 1
        if evs and evs[-1].state == "ok":
            recovered = True
            break
    assert recovered, "持续光照变化后应自适应恢复,而不是永远降级"


def test_lighting_brief_flicker_does_not_adapt():
    q = QualityPerceiver(_cfg())
    ctx = Context({})
    for i in range(CONFIRM):
        q.process(_frame(100, i), 0.1 * i, ctx)
    for i in range(CONFIRM):  # 短暂变亮 → 降级
        q.process(_frame(170, 10 + i), 1.0 + 0.1 * i, ctx)
    for i in range(LIGHT_ADAPT_AFTER - CONFIRM):  # 回到原亮度:恢复而不是记住"新基準"
        evs = q.process(_frame(100, 20 + i), 2.0 + 0.1 * i, ctx)
    assert q.degraded is False
    for i in range(CONFIRM):  # 再变亮 → 仍应立刻检出(基準没被闪烁污染)
        evs = q.process(_frame(170, 60 + i), 9.0 + 0.1 * i, ctx)
    assert q.degraded and q.reason == "lighting_drift"


def test_lighting_disabled_when_none():
    q = QualityPerceiver(QualityCfg(enabled=True, blur_min=2.0, brightness=(0, 255), lighting_drift=None, every_n=1))
    ctx = Context({})
    for i in range(20):
        assert q.process(_frame(100 + 8 * i, i), 0.1 * i, ctx) == []


def test_signal_event_binds_sn_in_active_cycle():
    """外部信号入口的引擎语义:周期内收 sn 信号 → 绑定到周期(扫码绑定场景)。"""
    from visual.dsl import CycleDef, Program, SignalCond, Zone, Cond
    prog = Program("ST", [], CycleDef(start=Cond(signal=SignalCond(name="go")), end=Cond(signal=SignalCond(name="end"))),
                   {}, "h")
    e = Engine(prog)
    e.process(Event(1.0, SIGNAL, name="go"))
    assert e.cycle is not None and e.cycle.sn is None
    e.process(Event(2.0, SIGNAL, name="sn", value="SN001"))
    assert e.cycle.sn == "SN001"


def test_templates_list_and_render():
    ts = list_templates()
    assert {"inspection", "station"} <= set(ts)
    insp_ids = {t["id"] for t in ts["inspection"]}
    assert {"glove", "first_article", "gauge_pointer"} <= insp_ids
    yaml_text = render_station("patrol_post", {"id": "SEC-01", "name": "东门岗", "line": "L1", "rtsp": "rtsp://cam/1"})
    assert "SEC-01" in yaml_text and "person_absent" in yaml_text and "$id" not in yaml_text
    # 未提供的参数保留 $token 原样,一眼看出没填
    assert "$rtsp" in render_station("patrol_post", {"id": "X"})
    import pytest
    with pytest.raises(KeyError):
        render_station("nope", {})


def test_templates_api_endpoints(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    with TestClient(build_app([], data)) as client:
        r = client.get("/api/templates")
        assert r.status_code == 200 and "patrol_post" in {t["id"] for t in r.json()["station"]}
        r = client.get("/api/templates/station/patrol_post", params={"id": "SEC-9", "rtsp": "rtsp://x"})
        assert r.status_code == 200 and "SEC-9" in r.json()["yaml"]
        assert client.get("/api/templates/station/nope").status_code == 404
