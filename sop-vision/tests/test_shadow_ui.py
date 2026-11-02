"""影子真值端点 + 时间轴视频端点测试。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from visual.server import build_app
from visual.shadow import ShadowStore


def test_shadow_truth_endpoints(tmp_path):
    store = ShadowStore(tmp_path / "data" / "shadow.db")  # 与端点同库
    i1 = store.add("sop_cycle", "ST", "C1", "PASS", None, {})
    i2 = store.add("inspection", "sc", "T1", "NG", 0.9, {})
    store.close()
    app = build_app([], tmp_path / "data")
    with TestClient(app) as c:
        rows = c.get("/api/shadow/samples", params={"unlabeled": 1}).json()
        assert {r["id"] for r in rows} == {i1, i2}
        assert c.post(f"/api/shadow/{i1}/truth", json={"truth": "NG"}).status_code == 200
        rows = c.get("/api/shadow/samples", params={"unlabeled": 1}).json()
        assert i1 not in {r["id"] for r in rows}  # 已标真值不再出现
        assert c.post(f"/api/shadow/{i2}/truth", json={"truth": "WRONG"}).status_code == 400


def test_timeline_video_served(tmp_path):
    # 直接复用 timeline e2e 的落库方式:造视频文件+记录
    import cv2
    import numpy as np
    from visual.timeline import TimelineStore
    vid = tmp_path / "data" / "timelines" / "v.mp4"  # 与端点同根
    vid.parent.mkdir(parents=True)
    w = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
    w.write(np.zeros((240, 320, 3), np.uint8))
    w.release()
    store = TimelineStore(tmp_path / "data" / "timelines.db")  # 与端点同库
    rec = store.create("t", "v.mp4", 1.0, 25.0)
    store.close()
    app = build_app([], tmp_path / "data")
    with TestClient(app) as c:
        r = c.get(f"/api/timelines/{rec['id']}/video")
        assert r.status_code == 200 and len(r.content) > 0
