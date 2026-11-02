from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from visual.dsl import HandsCfg, Zone
from visual.perception.base import Context, build_geoms

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "hand_landmarker.task"

pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="缺少 models/hand_landmarker.task（见 README“手部模型”）")


def test_mediapipe_pipeline_loads_and_blank_frame_yields_no_hands():
    from visual.perception.hands import MediaPipeHands

    zones = [Zone(id="Z", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)])]
    geoms = build_geoms(zones, 640, 480)
    hands = MediaPipeHands(HandsCfg(), geoms, ROOT)
    ctx = Context(geoms)
    try:
        for i in range(3):
            ctx.reset()
            assert hands.process(np.zeros((480, 640, 3), np.uint8), i * 0.04, ctx) == []
            assert ctx.hand_boxes == []
    finally:
        hands.close()


def test_missing_model_gives_actionable_error(tmp_path):
    from visual.perception.hands import MediaPipeHands

    zones = [Zone(id="Z", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)])]
    with pytest.raises(FileNotFoundError, match="hand_landmarker.task"):
        MediaPipeHands(HandsCfg(model="nope/none.task"), build_geoms(zones, 64, 48), tmp_path)
