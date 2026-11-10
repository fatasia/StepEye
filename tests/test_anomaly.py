"""AnomalyDINO 式免训练异常检测测试:合成 OK/NG 图判别、阈值、序列化。零权重依赖(网格特征)。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from visual.anomaly import AnomalyEngine, PatchMemory

np.random.seed(7)


def _ok_img() -> np.ndarray:
    """好品:均匀蓝底 + 固定白色方块(稳定工艺特征)。"""
    img = np.full((112, 112, 3), (180, 120, 60), np.uint8)  # BGR 蓝底
    img[20:60, 30:80] = (240, 240, 240)
    return img


def _ng_img(kind: str = "scratch") -> np.ndarray:
    """坏品:好品底 + 明显异常(划痕/色斑)。"""
    img = _ok_img()
    if kind == "scratch":
        cv2.line(img, (5, 5), (107, 100), (0, 0, 0), 6)  # 黑划痕
    elif kind == "stain":
        cv2.circle(img, (70, 70), 18, (0, 0, 200), -1)  # 红斑
    return img


def test_memory_nearest_neighbor_distance():
    m = PatchMemory()
    bank = np.random.RandomState(0).normal(0, 1, (20, 8)).astype(np.float32)
    m.add(bank)
    near, _ = m.score(bank[:5])  # 自身特征:距离应≈0
    assert near < 0.05
    far, fmax = m.score(np.random.RandomState(1).normal(0, 1, (6, 8)).astype(np.float32))
    assert far > 0.5  # 随机新特征距离大


def test_engine_pass_vs_ng_separated(tmp_path):
    eng = AnomalyEngine(threshold=0.05)
    for seed in range(4):
        img = _ok_img().copy()
        noise = np.random.RandomState(seed).normal(0, 6, img.shape).astype(np.float32)
        eng.add_ok(np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8))
    ok_out = eng.inspect(_ok_img())
    assert ok_out["verdict"] == "PASS" and ok_out["n_refs"] == 64  # 4图×16网格patch
    ng_out = eng.inspect(_ng_img("scratch"))
    assert ng_out["verdict"] == "NG" and ng_out["score"] > ok_out["score"]
    ng2 = eng.inspect(_ng_img("stain"))
    assert ng2["verdict"] == "NG"


def test_engine_empty_memory_is_uncertain(tmp_path):
    eng = AnomalyEngine()
    out = eng.inspect(_ng_img())
    assert out["verdict"] == "UNCERTAIN"  # 没登记 OK 图:不硬判


def test_engine_save_load_roundtrip(tmp_path):
    eng = AnomalyEngine(threshold=0.05)
    eng.add_ok(_ok_img())
    eng.add_ok(_ok_img())
    eng.save(tmp_path)
    eng2 = AnomalyEngine.load(tmp_path)
    assert eng2.n_refs == eng.n_refs and eng2.threshold == eng.threshold
    a, b = eng.inspect(_ng_img("scratch")), eng2.inspect(_ng_img("scratch"))
    assert a["score"] == b["score"] and a["verdict"] == b["verdict"]  # 序列化后判定一致


def test_anomaly_api_register_and_inspect(tmp_path):
    """API 全流程:登记 OK 图 → 判定 → 影子库落样 + 告警入库。"""
    from fastapi.testclient import TestClient

    from visual.server import build_app
    from visual.shadow import ShadowStore
    data = tmp_path / "d"
    store = ShadowStore(data / "shadow.db")
    store.close()
    ok, buf_ok = cv2.imencode(".jpg", _ok_img())
    _, buf_ng = cv2.imencode(".jpg", _ng_img())
    with TestClient(build_app([], data)) as c:
        r = c.post("/api/anomaly/scratch/register",
                   files=[("files", ("o1.jpg", buf_ok.tobytes(), "image/jpeg")),
                          ("files", ("o2.jpg", buf_ok.tobytes(), "image/jpeg"))],
                   data={"threshold": "0.05"})
        assert r.status_code == 201 and r.json()["total_refs"] > 0
        r2 = c.post("/api/anomaly/scratch/inspect",
                    files={"image": ("t.jpg", buf_ng.tobytes(), "image/jpeg")})
        assert r2.status_code == 201, r2.text
        out = r2.json()
        assert out["verdict"] in ("NG", "PASS")
        assert len(c.get("/api/alerts").json()) == 1  # 判定结果已入告警中心
        # 未登记场景 → 409 明示
        bad = c.post("/api/anomaly/nos/inspect", files={"image": ("t.jpg", buf_ok.tobytes(), "image/jpeg")})
        assert bad.status_code == 409
