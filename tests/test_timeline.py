"""V11 时间轴标注测试:合成视频上传→区间校验(重叠/越界)→抽帧产物→manifest。"""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from visual.server import build_app
from visual.timeline import check_intervals


def _mp4(tmp: Path, seconds: float = 4.0, fps: float = 25.0) -> bytes:
    avi = tmp / "tmp.mp4"
    buf = cv2.VideoWriter(str(avi), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    assert buf.isOpened()
    for i in range(int(seconds * fps)):
        buf.write(np.full((240, 320, 3), (i * 7) % 255, np.uint8))
    buf.release()
    data = avi.read_bytes()
    avi.unlink()
    return data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # _mp4 的临时 avi 落 tmp
    app = build_app([], tmp_path / "data")
    with TestClient(app) as c:
        yield c


def test_check_intervals():
    ok = check_intervals([{"step": "S1", "start": 0.0, "end": 2.0},
                          {"step": "S2", "start": 2.0, "end": 3.5}], 4.0)
    assert ok["ok"] and ok["problems"] == []
    bad = check_intervals([{"step": "S1", "start": 1.0, "end": 0.5},
                           {"step": "S2", "start": 0.0, "end": 2.0},
                           {"step": "S3", "start": 3.0, "end": 9.0}], 4.0)
    assert not bad["ok"] and len(bad["problems"]) == 3  # 逆序/重叠/越界


def test_timeline_end_to_end(client):
    r = client.post("/api/timelines", data={"name": "装配示教"},
                    files={"video": ("demo.mp4", _mp4(client.app.state.manager.data_dir.parent), "video/mp4")})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["duration_s"] > 3.0 and rec["status"] == "draft"
    tid = rec["id"]
    # 越界区间拒绝
    bad = client.post(f"/api/timelines/{tid}/intervals",
                      json={"intervals": [{"step": "S1", "start": 0.0, "end": 99.0}]})
    assert bad.status_code == 400 and "超出" in bad.json()["detail"]
    # 合法区间 → 抽帧 2×2=4 张进 manifest
    ok = client.post(f"/api/timelines/{tid}/intervals", json={
        "intervals": [{"step": "S1", "start": 0.2, "end": 1.8}, {"step": "S2", "start": 2.0, "end": 3.6}],
        "per_interval": 2})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["extracted"] == 4 and body["status"] == "annotated" and body["check"]["ok"]
    rows = [json.loads(l) for l in (client.app.state.manager.data_dir / "timelines" / tid / "frames" / "samples.manifest.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    assert {r0["label"] for r0 in rows} == {"S1", "S2"} and len(rows) == 4
    # 抽帧可回读
    img_url = body["frames_url_prefix"] + "/" + rows[0]["file"]
    assert client.get(img_url).status_code == 200

def test_registered_media_import_does_not_inherit_machine_truth(client):
    root = client.app.state.manager.data_dir
    pid = client.post("/api/label-projects", json={"name":"evidence", "classes":["part"]}).json()["id"]
    sample = root / "samplegen/batch"; sample.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(sample / "generated.jpg"), np.zeros((20, 20, 3), np.uint8))
    route = f"/api/label-projects/{pid}/import-evidence"
    row = client.post(route, json={"kind":"sample","name":"generated.jpg"}).json()
    assert row["synthetic"] and row["state"] == "unlabeled" and not row["targets"]
    assert client.post(route, json={"kind":"sample","name":"../generated.jpg"}).status_code == 400
    assert client.post(route, json={"kind":"timeline","source_id":"missing","name":"x.jpg"}).status_code == 404
