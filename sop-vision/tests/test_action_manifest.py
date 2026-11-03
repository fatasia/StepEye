import pytest
from visual.timeline import TimelineStore, check_intervals
from tools.action_manifest import build


def test_action_manifest_requires_disjoint_sources(tmp_path):
    src = tmp_path / "timelines"
    src.mkdir()
    (src / "video.mp4").write_bytes(b"source fixture")
    db = TimelineStore(tmp_path / "timelines.db")
    a, b = [db.create(name, "video.mp4", 5, 25)["id"] for name in ("a", "b")]
    for tid in (a, b):
        db.update(tid, status="annotated", intervals=[{"start": 1, "end": 3, "step": "取出"}])
    db.close()
    groups = {a: {"group": "day", "split": "train"}}
    assert build(tmp_path, groups)["clips"][0]["action"] == "取出"
    groups[b] = {"group": "independent?", "split": "val"}
    with pytest.raises(ValueError, match="跨越"):
        build(tmp_path, groups)


@pytest.mark.parametrize("iv", [{"step":"a","start":None,"end":2},{"step":"a","start":1,"end":float("nan")},{"step":" ","start":1,"end":2}])
def test_invalid_segment_rejected(iv):
    assert not check_intervals([iv], 5)["ok"]
