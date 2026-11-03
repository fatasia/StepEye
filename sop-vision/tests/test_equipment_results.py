"""原判/图片原子导入与独立复判；测试provider仅验证复用路径，不宣称AI准确率。"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import time

from PIL import Image
import pytest

from visual.equipment_results import EquipmentResults
from visual.inspection import InspectionService, InspectionStore, Scenario
from visual.labels import LabelError


def metadata(**changes):
    return {"device_id":"AOI-01","sn":"BOARD-0001","observed_at":"2026-11-03T12:00:00+08:00",
            "result":"NG","defects":["short"],"source_kind":"AOI","idempotency_key":"device-seq-1"}|changes


def picture(color="red"):
    buf=io.BytesIO();Image.new("RGB",(80,60),color).save(buf,"PNG");return ("board.png",buf.getvalue())


@pytest.fixture
def store(tmp_path):
    s=EquipmentResults(tmp_path/"equipment")
    yield s
    s.close()


def test_atomic_idempotent_import_preserves_original_and_reopens(store):
    before=picture();result,created=store.ingest(metadata(),[before],"operator")
    assert created and result["result"]=="NG" and result["human_truth"] is False
    assert result["observed_at"]=="2026-11-03T04:00:00Z"
    assert (store.records/result["id"]/"0.original").read_bytes()==before[1]
    assert store.image_path(result["id"],0).is_file()
    again,created=store.ingest(metadata(),[before],"other-user")
    assert not created and again==result
    assert store.list(device_id="AOI-01",sn="BOARD-0001",result="NG")["total"]==1
    root=store.root;store.close();other=EquipmentResults(root)
    try:assert other.get(result["id"])==result
    finally:other.close()


@pytest.mark.parametrize("change",[{"result":"OK"},{"defects":["open"]},{"sn":"different"}])
def test_same_key_different_payload_is_conflict(store,change):
    store.ingest(metadata(),[picture()],"operator")
    with pytest.raises(LabelError) as exc:store.ingest(metadata(**change),[picture()],"operator")
    assert exc.value.status==409 and store.list()["total"]==1


def test_same_key_changed_image_conflicts_and_concurrent_retries_create_once(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda _:store.ingest(metadata(),[picture()],"operator"),range(8)))
    assert sum(created for _,created in rows)==1 and len({r["id"] for r,_ in rows})==1
    with pytest.raises(LabelError):store.ingest(metadata(),[picture("blue")],"operator")
    assert len(list(store.records.iterdir()))==1 and not list(store.staging.iterdir())


def test_one_bad_image_rolls_back_result_and_all_files(store):
    with pytest.raises(LabelError):store.ingest(metadata(),[picture(),("bad.png",b"not an image")],"operator")
    assert store.list()["total"]==0
    assert not list(store.records.iterdir()) and not list(store.staging.iterdir())


def test_original_is_immutable_and_human_review_is_separate_and_idempotent(store):
    result,_=store.ingest(metadata(),[picture()],"operator")
    review={"verdict":"PASS","reason":"人工复核为反光","idempotency_key":"human-1"}
    first=store.review(result["id"],review,"reviewer")
    assert first["producer"]=="human" and first["actor"]=="reviewer"
    assert store.review(result["id"],review,"other")==first
    with pytest.raises(LabelError):store.review(result["id"],review|{"verdict":"NG"},"reviewer")
    assert store.get(result["id"])["result"]=="NG"
    assert store.reviews(result["id"])["items"]==[first]
    with pytest.raises(sqlite3.IntegrityError):store._db.execute("UPDATE equipment_results SET verdict='OK'")
    with pytest.raises(sqlite3.IntegrityError):store._db.execute("DELETE FROM equipment_reviews")


def test_tampered_normalized_image_is_not_served_or_rechecked(store):
    result,_=store.ingest(metadata(),[picture()],"operator")
    store.image_path(result["id"],0).write_bytes(b"changed")
    with pytest.raises(LabelError) as exc:store.image_path(result["id"],0)
    assert exc.value.status==409


def test_machine_recheck_uses_existing_inspection_once_without_overwriting_device(store,tmp_path):
    class Provider:
        calls=0
        def inspect(self,images,prompt):
            self.calls+=1
            return {"ok":True,"verdict":"PASS","confidence":.8,"reason":"test provider fixture","raw":"{}"}
    provider=Provider()
    inspection=InspectionService([Scenario("aoi-recheck","AOI review",prompt="inspect",enabled=True)],
        InspectionStore(tmp_path/"inspection.db"),tmp_path/"images",provider)
    inspection.start()
    try:
        result,_=store.ingest(metadata(),[picture()],"operator")
        body={"scenario_id":"aoi-recheck","idempotency_key":"recheck-1"}
        first=store.recheck(result["id"],body,"operator",inspection)
        assert first["status"]=="submitted" and first["producer"]=="machine_suggestion"
        assert store.recheck(result["id"],body,"operator",inspection)==first
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and inspection.store.get(first["task_id"])["status"] not in {"done","failed"}:time.sleep(.01)
        joined=store.reviews(result["id"],inspection)["items"][0]
        assert joined["inspection"]["status"]=="done" and joined["inspection"]["verdict"]=="PASS"
        assert provider.calls==1 and store.get(result["id"])["result"]=="NG"
        with pytest.raises(LabelError) as exc:store.recheck(result["id"],body|{"scenario_id":"other"},"operator",inspection)
        assert exc.value.status==409
    finally:inspection.stop(close_store=True)


def test_recheck_missing_provider_is_explicit_without_changing_original(store):
    result,_=store.ingest(metadata(),[picture()],"operator")
    with pytest.raises(LabelError) as exc:
        store.recheck(result["id"],{"scenario_id":"scene","idempotency_key":"one"},"operator",None)
    assert exc.value.status==503
    assert store.reviews(result["id"])["items"]==[] and store.get(result["id"])["result"]=="NG"


def test_public_fixed_image_ingestion_keeps_device_assertion_separate_from_ai_truth(store):
    image=Path(__file__).resolve().parents[2]/".v0/quality-models/deeppcb/PCBData/group00041/00041/00041000_test.jpg"
    if not image.is_file():pytest.skip("downloaded public fixed image not present")
    result,_=store.ingest(metadata(idempotency_key="public-fixed"),[(image.name,image.read_bytes())],"test")
    assert result["images"][0]["original_sha256"]=="624b448f0737aaa8bdc53963a752800147bffb2591365b9810b3f2a83b956c4a"
    assert result["result"]=="NG" and result["human_truth"] is False


def test_crash_after_inspection_dispatch_never_automatically_spends_inference_again(store,tmp_path):
    class Provider:
        calls=0
        def inspect(self,*args):
            self.calls+=1
            return {"ok":True,"verdict":"NG","confidence":.9,"reason":"fixture"}
    provider=Provider()
    inspection=InspectionService([Scenario("scene","test",prompt="test",enabled=True)],
        InspectionStore(tmp_path/"inspection.db"),tmp_path/"images",provider)
    inspection.start()
    try:
        result,_=store.ingest(metadata(),[picture()],"operator")
        store._db.execute("CREATE TRIGGER fault_after_dispatch BEFORE UPDATE ON equipment_rechecks BEGIN SELECT RAISE(ABORT,'test interrupted association'); END")
        body={"scenario_id":"scene","idempotency_key":"dispatch-crash"}
        with pytest.raises(sqlite3.IntegrityError):store.recheck(result["id"],body,"operator",inspection)
        store._db.execute("DROP TRIGGER fault_after_dispatch")
        pending=store.recheck(result["id"],body,"operator",inspection)
        assert pending["status"]=="dispatch_unknown" and pending["task_id"] is None
        deadline=time.monotonic()+3
        while not provider.calls and time.monotonic()<deadline:time.sleep(.01)
        assert provider.calls==1 and len(inspection.store.list())==1
        assert store.get(result["id"])["result"]=="NG"
    finally:inspection.stop(close_store=True)
