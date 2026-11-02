from __future__ import annotations

import numpy as np

from visual.evidence import CycleStore, EvidenceRecorder


def test_evidence_frames_are_taken_at_flush_time_not_event_time(tmp_path):
    """回归：证据帧曾取引擎事件回调那一刻的旧标注图，状态文字比事件慢一帧。"""
    current = {"v": 0}
    store = CycleStore(tmp_path / "db.sqlite")
    rec = EvidenceRecorder(tmp_path / "ev", store, lambda: np.full((8, 8, 3), current["v"], np.uint8))

    rec.on_event("cycle_start", {"cycle_id": "C1", "ts": 0.0})
    rec.on_event("step_done", {"cycle_id": "C1", "step": "S1", "ts": 1.0})
    assert not (tmp_path / "ev" / "C1").exists(), "事件回调时不应立即落盘"

    current["v"] = 200  # 本帧标注图在事件回调之后才更新
    rec.flush()
    import cv2
    img = cv2.imread(str(tmp_path / "ev" / "C1" / "frames" / "02_step_done_S1.jpg"))
    assert img is not None and img.mean() > 150, "证据帧应是 flush 时的最新画面"

    current["v"] = 90
    rec.on_event("cycle_end", {"cycle_id": "C1", "ts": 2.0, "station": "S", "result": "PASS", "start_wall": "a",
                               "end_wall": "b", "duration_s": 2.0, "steps": [], "violations": []})
    assert store.get("C1") is None, "周期结束的入库也应等到 flush"
    rec.flush()
    got = store.get("C1")
    assert got["result"] == "PASS" and [f["tag"] for f in got["frames"]] == ["cycle_start", "step_done", "cycle_end"]
    assert got["sha256"]
    store.close()
