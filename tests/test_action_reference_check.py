"""参考卡 × 动作影子对账：match/deviation/no_data 三路、幂等去重、不可变。"""
import pytest

from visual.action_reference_check import ReferenceChecks, evaluate


@pytest.fixture
def store(tmp_path):
    store = ReferenceChecks(tmp_path / "checks.db")
    yield store
    store.close()


def reference(station="ST-A", step="fasten", version=1):
    return {"id": f"ref-{station}-{step}-{version}", "station_id": station, "step_id": step,
            "step_name": "锁附", "version": version}


def test_three_verdicts_and_mapping_missing_is_no_data():
    records = [reference()]
    obs_match = [{"step_id": "fasten", "cycle_id": "c1", "label": "拧紧", "confidence": .9}]
    obs_wrong = [{"step_id": "fasten", "cycle_id": "c2", "label": "焊接", "confidence": .9}]
    obs_low = [{"step_id": "fasten", "cycle_id": "c3", "label": "拧紧", "confidence": .3}]
    assert evaluate(records, {"fasten": "拧紧"}, obs_match)[0]["verdict"] == "match"
    assert evaluate(records, {"fasten": "拧紧"}, obs_wrong)[0]["verdict"] == "deviation"
    assert evaluate(records, {"fasten": "拧紧"}, obs_low)[0]["verdict"] == "deviation"  # 低于 min_conf 不算命中
    assert evaluate(records, {}, obs_match)[0]["verdict"] == "no_data"  # 映射未配置不硬套


def test_unobserved_step_emits_no_data_with_empty_cycle():
    verdicts = evaluate([reference()], {"fasten": "拧紧"}, [])
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "no_data" and verdicts[0]["cycle_id"] == ""
    assert verdicts[0]["expected_label"] == "拧紧"  # 映射仍在,如实透出


def test_uses_latest_reference_version_and_keeps_station_scope():
    records = [reference(version=1), reference(version=2)]
    obs = [{"step_id": "fasten", "cycle_id": "c1", "label": "拧紧", "confidence": .9}]
    record = evaluate(records, {"fasten": "拧紧"}, obs)[0]
    assert record["reference_version"] == 2
    other = evaluate([reference(station="ST-B")], {}, [])[0]
    assert other["station_id"] == "ST-B"


def test_store_idempotent_and_immutable(tmp_path, store):
    records = [reference()]
    obs = [{"step_id": "fasten", "cycle_id": "c1", "label": "拧紧", "confidence": .9}]
    first_run = evaluate(records, {"fasten": "拧紧"}, obs)
    first, created = store.create(first_run[0])
    assert created is True
    again, created = store.create({**first_run[0], "id": "RC-另一次评估"})
    assert created is False and again["id"] == first["id"]  # 同键返回已有记录
    with pytest.raises(Exception, match="immutable"):
        store._db.execute("UPDATE reference_checks SET body='x'")


def test_list_filters_by_station(store):
    for station in ("ST-A", "ST-B"):
        store.create(evaluate([reference(station=station)], {}, [])[0])
    assert len(store.list("ST-A")) == 1 and store.list("ST-A")[0]["station_id"] == "ST-A"
    assert len(store.list()) == 2
    with pytest.raises(ValueError):
        store.list(limit=0)


def test_input_validation():
    with pytest.raises(ValueError, match="mapping"):
        evaluate([reference()], {"fasten": "  "}, [])
    with pytest.raises(ValueError, match="缺少字段"):
        evaluate([reference()], {}, [{"step_id": "fasten"}])
    with pytest.raises(ValueError, match="label"):
        evaluate([reference()], {"fasten": "x"}, [{"step_id": "fasten", "cycle_id": "c", "label": 3}])
