from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from visual.patrol import LOCAL_TZ, PatrolPlan, PatrolStore, window_for


def ts(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=LOCAL_TZ).timestamp()


def plan(**kwargs):
    return PatrolPlan(name="东门早巡", station="ST", zone="gate", start="08:00", end="08:01", min_presence_s=10, **kwargs)


@pytest.fixture
def store(tmp_path):
    s = PatrolStore(tmp_path / "patrol.db")
    s.add_plan(plan(), at=ts("2026-11-02T00:00:00"))
    yield s
    s.close()


def test_no_observations_are_unknown_not_missed(store):
    out = store.report("2026-11-03", now=ts("2026-11-03T09:00:00"))
    assert out["rows"][0]["status"] == "unknown"
    assert out["counts"]["missed"] == 0 and out["rows"][0]["coverage"] == 0


def test_presence_must_be_continuous_and_evidence_is_persisted(store):
    start = ts("2026-11-03T08:00:00")
    for n in range(61):
        store.observe("ST", "gate", 1 if 12 <= n <= 25 else 0, start + n)
    out = store.report("2026-11-03", now=start + 70)["rows"][0]
    assert out["status"] == "arrived" and out["longest_presence_s"] == 13
    assert out["arrived_at"].endswith("08:00:22+08:00")
    assert out["evidence"]["kind"] == "person_count_samples"
    assert out["coverage"] == 1.0


def test_fully_observed_empty_window_is_missed(store):
    start = ts("2026-11-03T08:00:00")
    for n in range(61):
        store.observe("ST", "gate", 0, start + n)
    out = store.report("2026-11-03", now=start + 70)["rows"][0]
    assert out["status"] == "missed" and out["observed_s"] == 60


def test_offline_gap_never_extends_presence_or_absence(store):
    start = ts("2026-11-03T08:00:00")
    for n in [0, 1, 2, 50, 51, 52]:
        store.observe("ST", "gate", 1, start + n)
    out = store.report("2026-11-03", now=start + 70)["rows"][0]
    assert out["status"] == "unknown" and out["longest_presence_s"] == 2
    assert out["present_s"] == 4


def test_uncertain_sample_breaks_continuity(store):
    start = ts("2026-11-03T08:00:00")
    for n in range(16):
        store.observe("ST", "gate", None if n == 7 else 1, start + n)
    out = store.report("2026-11-03", now=start + 70)["rows"][0]
    assert out["status"] == "unknown" and out["longest_presence_s"] < 10


def test_future_and_running_windows_are_pending(store):
    assert store.report("2026-11-03", now=ts("2026-11-03T07:00:00"))["rows"][0]["status"] == "pending"
    assert store.report("2026-11-03", now=ts("2026-11-03T08:00:30"))["rows"][0]["status"] == "pending"


def test_cross_midnight_uses_start_date_and_fixed_timezone(tmp_path):
    s = PatrolStore(tmp_path / "patrol.db")
    p = PatrolPlan(name="夜巡", station="ST", zone="gate", start="23:59", end="00:01", min_presence_s=5)
    start, end = window_for(p, datetime(2026, 11, 3).date())
    assert end - start == 120
    s.add_plan(p, at=start - 86400)
    for n in range(7):
        s.observe("ST", "gate", 1, ts("2026-11-04T00:00:00") + n)
    row = s.report("2026-11-03", now=end + 1)["rows"][0]
    assert row["status"] == "arrived" and row["window_end"].startswith("2026-11-04T00:01")
    s.close()


@pytest.mark.parametrize("change", [{"start": "25:00"}, {"start": "08:01"}, {"min_presence_s": 61},
                                    {"min_presence_s": float("nan")}, {"name": "  "}])
def test_invalid_plans_rejected(change):
    raw = plan().model_dump(); raw.update(change)
    with pytest.raises(ValidationError):
        PatrolPlan.model_validate(raw)


def test_delete_is_soft_and_history_remains(store):
    pid = store.plans()[0]["id"]
    assert store.delete_plan(pid, at=ts("2026-11-04T00:00:00"))
    assert store.plans() == []
    assert len(store.report("2026-11-03", now=ts("2026-11-04T01:00:00"))["rows"]) == 1
    assert store.report("2026-11-05", now=ts("2026-11-06T01:00:00"))["rows"] == []


def test_observations_persist_and_duplicate_timestamp_is_ignored(tmp_path):
    path = tmp_path / "patrol.db"
    s = PatrolStore(path)
    s.add_plan(plan(), at=ts("2026-11-02T00:00:00"))
    start = ts("2026-11-03T08:00:00")
    for n in range(12):
        s.observe("ST", "gate", 1, start + n)
        s.observe("ST", "gate", 1, start + n)
    s.close()
    reopened = PatrolStore(path)
    assert reopened.report("2026-11-03", now=start + 70)["rows"][0]["status"] == "arrived"
    reopened.close()


def test_runtime_hook_skips_sim_file_and_bad_quality(store):
    rt = SimpleNamespace(cfg=SimpleNamespace(station="ST", source=SimpleNamespace(type="sim"),
                        persons=SimpleNamespace(enter_frames=2, exit_frames=4)), persons=SimpleNamespace(
                        tracker=SimpleNamespace(count={"gate": 1})), frame_idx=10,
                        engine=SimpleNamespace(world=SimpleNamespace(quality_bad=False)))
    start = ts("2026-11-03T08:00:00")
    store.observe_runtime(rt, start)
    rt.cfg.source.type = "file"; store.observe_runtime(rt, start + 1)
    assert store._db.execute("SELECT COUNT(*) FROM patrol_observations").fetchone()[0] == 0
    rt.cfg.source.type = "rtsp"; store.observe_runtime(rt, start + 2)
    store.observe_runtime(rt, start + 2.2)  # 1Hz 节流
    rt.engine.world.quality_bad = True; store.observe_runtime(rt, start + 3)
    samples = store._db.execute("SELECT count FROM patrol_observations ORDER BY at").fetchall()
    assert samples == [(1,), (None,)]


def test_plan_not_retroactive_before_creation(tmp_path):
    s = PatrolStore(tmp_path / "patrol.db")
    s.add_plan(plan(), at=ts("2026-11-04T00:00:00"))
    assert s.report("2026-11-03", now=ts("2026-11-05T00:00:00"))["rows"] == []
    s.close()


def test_short_outage_can_hide_complete_patrol_even_when_coverage_high(tmp_path):
    s = PatrolStore(tmp_path / "patrol.db")
    p = plan().model_dump(); p["end"] = "08:10"
    start = ts("2026-11-03T08:00:00")
    s.add_plan(p, at=start - 86400)
    for n in range(601):
        if not 100 <= n < 120:
            s.observe("ST", "gate", 0, start + n)
    row = s.report("2026-11-03", now=start + 700)["rows"][0]
    assert row["coverage"] > 0.95
    assert row["status"] == "unknown" and row["longest_unknown_s"] >= 20
    s.close()
