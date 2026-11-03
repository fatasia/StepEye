"""真实build_app的设备导入/查询/复判权限，模型不可用不调用外部服务。"""
import io
import json

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from visual.auth import AuthStore
from visual.server import build_app


def payload(**changes):
    return {"device_id":"AOI-001","sn":"SN-1001","observed_at":"2026-11-03T10:00:00Z",
            "result":"NG","defects":["short"],"source_kind":"AOI","idempotency_key":"frame-1"}|changes


def png():
    data=io.BytesIO();Image.new("RGB",(64,48),"gray").save(data,"PNG");return data.getvalue()


def ingest(client,body=None,data=None):
    return client.post("/api/equipment-results",data={"metadata":json.dumps(body or payload())},
                       files=[("files",("board.png",data or png(),"image/png"))])


@pytest.fixture
def api(tmp_path):
    auth=AuthStore(tmp_path/"auth.db")
    for role in ("admin","operator","viewer"):auth.set_user(role,"test-password",role)
    app=build_app([],tmp_path/"data",auth=auth)
    with TestClient(app) as client:yield client,app.state.manager,auth
    auth.close()


def login(client,role):
    assert client.post("/api/auth/login",json={"username":role,"password":"test-password"}).status_code==200


def test_real_auth_and_upload_association_query_manual_review(api):
    client,manager,auth=api
    assert client.get("/api/equipment-results").status_code==401
    assert ingest(client).status_code==401
    login(client,"viewer")
    assert client.get("/api/equipment-results").status_code==200
    assert ingest(client).status_code==403
    login(client,"operator")
    response=ingest(client);assert response.status_code==201
    record=response.json()["result"];rid=record["id"]
    assert response.json()["created"] is True and record["actor"]=="operator"
    duplicate=ingest(client);assert duplicate.json()["created"] is False
    assert duplicate.json()["result"]["id"]==rid
    assert client.get("/api/equipment-results?device_id=AOI-001&sn=SN-1001&result=NG").json()["total"]==1
    assert client.get(record["image_urls"][0]).headers["content-type"]=="image/png"
    review={"verdict":"PASS","reason":"独立人工复核","idempotency_key":"review-1"}
    checked=client.post(f"/api/equipment-results/{rid}/reviews",json=review)
    assert checked.status_code==201 and checked.json()["producer"]=="human"
    assert client.get(f"/api/equipment-results/{rid}").json()["result"]=="NG"
    login(client,"viewer")
    assert client.post(f"/api/equipment-results/{rid}/reviews",json=review).status_code==403
    assert client.post(f"/api/equipment-results/{rid}/rechecks",json={"scenario_id":"scene","idempotency_key":"x"}).status_code==403
    reviews=client.get(f"/api/equipment-results/{rid}/reviews")
    assert reviews.status_code==200 and reviews.json()["items"][0]["verdict"]=="PASS"


def test_conflicting_or_invalid_ingest_and_unknown_image_are_bounded(api):
    client,manager,auth=api;login(client,"operator")
    result=ingest(client).json()["result"]
    assert ingest(client,payload(result="OK")).status_code==409
    assert ingest(client,payload(idempotency_key="bad-image"),b"not-image").status_code==400
    assert ingest(client,payload(observed_at="2026-11-03T10:00:00")).status_code==400
    assert ingest(client,payload(command="not-allowed")).status_code==400
    assert client.get("/api/equipment-results").json()["total"]==1
    assert client.get(f"/api/equipment-results/{result['id']}/images/9").status_code==404
    assert client.get("/api/equipment-results/missing").status_code==404


def test_unconfigured_recheck_is_503_but_original_and_human_review_stay_readable(api,monkeypatch):
    client,manager,auth=api;login(client,"operator")
    result=ingest(client).json()["result"];rid=result["id"]
    monkeypatch.setattr(manager,"inspection",None)
    response=client.post(f"/api/equipment-results/{rid}/rechecks",json={"scenario_id":"scene","idempotency_key":"one"})
    assert response.status_code==503
    assert client.get(f"/api/equipment-results/{rid}").json()["result"]=="NG"
    assert client.get(f"/api/equipment-results/{rid}/reviews").json()=={"items":[]}


def test_mcp_label_token_has_no_equipment_result_or_image_access(api):
    client,manager,auth=api
    p=manager.labels.create_project("scope",["part"])
    token=auth.issue_mcp_token("admin","scope",["read","images","draft"],[p["id"]])
    headers={"Authorization":"Bearer "+token["token"]}
    assert client.get("/api/equipment-results",headers=headers).status_code==403
    assert client.post("/api/equipment-results",headers=headers).status_code==403
