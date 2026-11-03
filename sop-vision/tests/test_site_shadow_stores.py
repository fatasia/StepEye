import sqlite3

import pytest

from visual.plans import PlanStore
from visual.shadow import ShadowStore


def test_plan_point_station_binding_and_delete_ownership(tmp_path):
    store = PlanStore(tmp_path / "plans.db")
    p1 = store.create("一厂", "a.jpg")
    p2 = store.create("二厂", "b.jpg")
    point = store.add_point(p1["id"], "东门", "camera", .2, .4, "ST01")
    assert store.points(p1["id"])[0]["station_id"] == "ST01"
    assert not store.delete_point(point["id"], p2["id"])
    assert store.delete_point(point["id"], p1["id"])
    with pytest.raises(ValueError, match="名称"):
        store.add_point(p1["id"], " ", "camera", .2, .3)
    with pytest.raises(ValueError, match="不存在"):
        store.add_point("NOPE", "东门", "camera", .2, .3)
    store.close()


def test_old_plan_database_migrates_station_binding(tmp_path):
    p = tmp_path / "old.db"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE plan_points (id TEXT PRIMARY KEY, plan_id TEXT, name TEXT, kind TEXT, x REAL, y REAL, created TEXT)")
    db.execute("INSERT INTO plan_points VALUES ('P','PL','东门','camera',.3,.4,'2026-11-03')")
    db.commit(); db.close()
    store = PlanStore(p)
    assert store.get_point("P")["station_id"] == ""
    store.close()


def test_shadow_sample_details_truth_not_found_and_uncertain_report(tmp_path):
    s = ShadowStore(tmp_path / "shadow.db")
    fail = s.add("sop_cycle", "ST", "C1", "FAIL", None, {"reason": "漏步"})
    uncertain = s.add("inspection", "SC", "T1", "UNCERTAIN", .2, {})
    assert s.get(fail)["system_verdict"] == "NG"
    assert s.get(fail)["body"]["reason"] == "漏步"
    assert not s.set_truth(999, "NG")
    with pytest.raises(ValueError):
        s.set_truth(fail, "FAIL")
    assert s.set_truth(fail, "NG")
    assert s.set_truth(uncertain, "PASS")
    assert not s.list()
    assert len(s.list(False)) == 2
    kinds = s.report()["kinds"]
    assert kinds["sop_cycle"]["correct"] == 1
    assert kinds["inspection"]["uncertain"] == 1
    assert kinds["inspection"]["false_alarm"] == 0
    assert kinds["inspection"]["agree_rate"] is None
    s.close()
