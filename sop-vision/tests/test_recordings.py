from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from visual.recordings import RecordingIndex, parse_time
from visual.server import build_app


def make_video(root, name="live_test_20261103_120000.mp4"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for _ in range(20):
        w.write(np.zeros((48, 64, 3), np.uint8))
    w.release()
    return path


def test_recording_overlap_and_stream_filter(tmp_path):
    make_video(tmp_path)
    make_video(tmp_path, "other_20261103_120000.mp4")
    idx = RecordingIndex(tmp_path)
    rows = idx.list("live/test", parse_time("2026-11-03T12:00:01+08:00"), parse_time("2026-11-03T12:01:00+08:00"))
    assert len(rows["rows"]) == 1 and rows["rows"][0]["duration_s"] == 2
    assert idx.list("live/test", parse_time("2026-11-03T12:01:00"), parse_time("2026-11-03T12:02:00"))["rows"] == []
    with pytest.raises(ValueError):
        idx.path("live/test", "../x.mp4")
    with pytest.raises(ValueError):
        idx.path("other", "live_test_20261103_120000.mp4")
    with pytest.raises(ValueError):
        idx.list("live/test", parse_time("2026-11-03"), parse_time("2026-11-01"))


def test_recordings_api_serves_range_and_rejects_bad_file(tmp_path, monkeypatch):
    recordings = tmp_path / "records"
    path = make_video(recordings)
    monkeypatch.setenv("MONIBUCA_RECORD_DIR", str(recordings))
    cfg = Path(__file__).resolve().parents[1] / "configs/demo_sim.yaml"
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    raw["source"]["preview_stream"] = "live/test"
    p = tmp_path / "st.yaml"; p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with TestClient(build_app([p], tmp_path / "data")) as c:
        url = "/api/stations/DEMO-ST01/recordings"
        rows = c.get(url, params={"start": "2026-11-03T11:59:00+08:00", "end": "2026-11-03T12:01:00+08:00"})
        assert rows.status_code == 200 and len(rows.json()["rows"]) == 1
        r = c.get(url + "/" + path.name, headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100
        assert r.headers["content-range"].startswith("bytes 0-99/")
        assert c.get(url + "/other_20261103_120000.mp4").status_code == 404
        assert c.get(url, params={"start": "bad", "end": "bad"}).status_code == 400
