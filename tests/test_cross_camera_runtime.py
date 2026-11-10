"""Runtime publication is detached and never turns an unavailable view into zero."""
import numpy as np

from test_runtime_health import runtime
from visual.tracking import ObjectTrack


class Detector:
    mot = object()
    class_names = ["person"]
    _last_frame_ts = None

    def process(self, frame, ts, ctx):
        if self._last_frame_ts == ts:
            return []
        self._last_frame_ts = ts
        ctx.object_tracks.append(ObjectTrack(1, "person", (1, 1, 20, 30), .9, ts, ts, 1))
        return []

    def reset_observations(self):
        self._last_frame_ts = None


def test_snapshot_detached_and_lifecycle(tmp_path):
    rt = runtime(tmp_path)
    rt.detector = Detector()
    rt._health_running = True
    rt.cfg.detector.classes = ["person"]
    try:
        assert rt.cross_camera_snapshot() is None
        rt.process_frame(1., np.zeros((240, 320, 3), dtype=np.uint8))
        first = rt.cross_camera_snapshot()
        assert first["available"] and len(first["tracks"]) == 1
        assert first["available_labels"] == ("person",)
        first["tracks"][0]["label"] = "changed"
        assert rt.cross_camera_snapshot()["tracks"][0]["label"] == "person"
        rt.process_frame(1., np.zeros((240, 320, 3), dtype=np.uint8))
        assert not rt.cross_camera_snapshot()["available"]
        rt._reset_visual_observations()
        assert rt.cross_camera_snapshot() is None
        rt.process_frame(2., np.zeros((240, 320, 3), dtype=np.uint8))
        rt._health_running = False
        assert not rt.cross_camera_snapshot()["available"]
    finally:
        rt.close()


def test_sim_without_detector_has_no_valid_empty_count(tmp_path):
    rt = runtime(tmp_path)
    try:
        rt.run(max_frames=1)
        row = rt.cross_camera_snapshot()
        assert row["source_type"] == "sim"
        assert not row["available"] and row["tracks"] == []
    finally:
        rt.close()
