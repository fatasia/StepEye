"""厂区点位绑定、真值详情与报告接口的回归。"""
import cv2
import numpy as np
from fastapi.testclient import TestClient

from visual.server import build_app
from visual.shadow import ShadowStore


def test_shadow_detail_report_and_missing_truth(tmp_path):
    data = tmp_path / "data"
    store = ShadowStore(data / "shadow.db")
    sid = store.add("sop_cycle", "ST", "C1", "FAIL", None, {"reason": "漏步"})
    store.close()
    with TestClient(build_app([], data)) as c:
        detail = c.get(f"/api/shadow/{sid}")
        assert detail.status_code == 200
        assert detail.json()["body"]["reason"] == "漏步"
        assert detail.json()["system_verdict"] == "NG"
        assert c.get("/api/shadow/999").status_code == 404
        assert c.post("/api/shadow/999/truth", json={"truth": "PASS"}).status_code == 404
        assert c.post(f"/api/shadow/{sid}/truth", json={"truth": "NG"}).status_code == 200
        report = c.get("/api/shadow/report")
        assert report.status_code == 200
        assert report.json()["kinds"]["sop_cycle"]["correct"] == 1


def test_plan_point_api_binding_and_scoped_delete(tmp_path):
    _, image = cv2.imencode(".png", np.zeros((100, 200, 3), np.uint8))
    with TestClient(build_app([], tmp_path / "data")) as c:
        def plan(name):
            return c.post("/api/plans", data={"name": name},
                          files={"image": ("p.png", image.tobytes(), "image/png")}).json()["id"]
        p1, p2 = plan("一厂"), plan("二厂")
        response = c.post(f"/api/plans/{p1}/points", json={"name": "东门", "kind": "camera", "x": .3, "y": .4,
                                                               "station_id": "ST"})
        assert response.status_code == 201
        point = response.json()
        assert point["station_id"] == "ST"
        assert c.delete(f"/api/plans/{p2}/points/{point['id']}").status_code == 404
        assert c.delete(f"/api/plans/{p1}/points/{point['id']}").status_code == 200
