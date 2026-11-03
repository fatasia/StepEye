"""JSON/CSV导入、持久报告、真实服务RBAC接线；不使用生产测量数据。"""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from visual.auth import AuthStore
from visual.quality_routes import AnalysisBody, QualityReports, import_rows
from visual.server import build_app


def spc():
    return {"kind":"spc","rows":[{"value":v} for v in [9,10,11,10]*8],
            "options":{"lsl":5,"usl":15,"baseline_count":24},"title":"Gap fixture","unit":"mm"}


def grr_csv():
    return "part,operator,trial,value\n"+"\n".join(
        f"P{i},O{j},{k},{v+b+r}" for i,v in enumerate([0,10,20])
        for j,b in enumerate([-1,1]) for k,r in enumerate([-.5,.5]))


def test_report_reopens_with_exact_input_hash_and_is_immutable(tmp_path):
    path=tmp_path/"quality.db"
    store=QualityReports(path)
    report=store.create(spc(),"analyst")
    assert report["actor"]=="analyst" and len(report["input_sha256"])==64
    assert report["inputs"]["rows"][0]=={"value":9}
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("UPDATE quality_reports SET kind='grr'")
    store.close(); store.close()
    store=QualityReports(path)
    try:
        assert store.get(report["id"])==report
        assert store.list(kind="spc")["total"]==1
        summary=store.list()["items"][0]
        assert "inputs" not in summary and "result" not in summary
        assert summary["status"]=="review_required"
    finally: store.close()


def test_csv_grr_and_json_agreement_are_actual_calculations(tmp_path):
    store=QualityReports(tmp_path/"quality.db")
    try:
        result=store.create({"kind":"grr","csv_text":grr_csv(),"options":{"lsl":-10,"usl":30}},"test")
        assert result["result"]["components"]["grr"]["variance"]==pytest.approx(2.5)
        rows=[{"part":p,"operator":o,"trial":t,"label":"OK" if p=="P1" else "NG"}
              for p in ["P1","P2"] for o in ["A","B"] for t in [1,2]]
        result=store.create({"kind":"agreement","rows":rows},"test")
        assert result["result"]["between_operator_per_trial"]==1
        assert result["result"]["pairwise_cohen_kappa"][0]["kappa"]==1
        assert store.list()["total"]==2
    finally: store.close()


def test_parallel_reports_do_not_overwrite_or_lose_rows(tmp_path):
    store=QualityReports(tmp_path/"quality.db")
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda i:store.create(spc(),f"actor-{i}"),range(12)))
        assert len({r["id"] for r in results})==12
        assert len({r["input_sha256"] for r in results})==1
        assert store.list(limit=5,offset=5)["total"]==12
        assert len(store.list(limit=5,offset=5)["items"])==5
    finally: store.close()


@pytest.mark.parametrize("csv_text",["value,value\n1,2","value,command\n1,run", "value\nnot-a-number",
                                    "value\n1,2", "defect,count\nx,1.5"])
def test_malformed_csv_rejected_without_execution(csv_text):
    with pytest.raises(ValueError): import_rows("pareto" if csv_text.startswith("defect") else "spc",csv_text)


def test_input_body_requires_exactly_one_source_and_unknown_fields_are_rejected(tmp_path):
    with pytest.raises(ValidationError): AnalysisBody(kind="spc")
    with pytest.raises(ValidationError): AnalysisBody(**spc(),csv_text="value\n1\n2")
    store=QualityReports(tmp_path/"quality.db")
    try:
        with pytest.raises(ValueError): store.create(spc()|{"options":{"command":"no"}},"test")
        with pytest.raises(ValueError): store.create(spc()|{"rows":[{"value":1,"unit":"mm"},{"value":2}]},"test")
        with pytest.raises(ValueError): store.create({"kind":"spc","csv_text":"value\n1\nNaN"},"test")
        assert store.list()["total"]==0
    finally: store.close()


@pytest.fixture
def api(tmp_path):
    auth=AuthStore(tmp_path/"auth.db")
    for role in ("admin","operator","viewer"): auth.set_user(role,"test-password",role)
    app=build_app([],tmp_path/"data",auth=auth)
    with TestClient(app) as client: yield client,app.state.manager,auth
    auth.close()


def login(client,role):
    assert client.post("/api/auth/login",json={"username":role,"password":"test-password"}).status_code==200


def test_real_api_rbac_create_list_detail_and_json_export(api):
    client,manager,auth=api
    assert client.get("/api/quality/capabilities").status_code==401
    assert client.post("/api/quality/analyses",json=spc()).status_code==401
    login(client,"viewer")
    assert client.get("/api/quality/capabilities").status_code==200
    assert client.post("/api/quality/analyses",json=spc()).status_code==403
    login(client,"operator")
    response=client.post("/api/quality/analyses",json=spc())
    assert response.status_code==201
    report=response.json(); assert report["actor"]=="operator"
    assert report["result"]["production_approved"] is False
    login(client,"viewer")
    path=f"/api/quality/analyses/{report['id']}"
    assert client.get(path).json()==report
    assert client.get("/api/quality/analyses?kind=spc").json()["total"]==1
    export=client.get(path+"/export")
    assert export.status_code==200 and export.json()==report
    assert "attachment; filename=\"QA-" in export.headers["content-disposition"]
    assert client.get("/api/quality/analyses/missing").status_code==404


def test_api_csv_import_and_validation_errors_leave_no_partial_report(api):
    client,manager,auth=api; login(client,"operator")
    response=client.post("/api/quality/analyses",json={"kind":"grr","csv_text":grr_csv()})
    assert response.status_code==201
    assert response.json()["result"]["design"]=={"parts":3,"operators":2,"trials":2,"observations":12}
    for body,code in [({"kind":"spc","csv_text":"value\n1\nNaN"},400),
                      (spc()|{"options":{"baseline_count":1}},400),
                      (spc()|{"command":"no"},422),
                      ({"kind":"grr","csv_text":"part,operator,trial,value\nP1,A,1,1"},400)]:
        assert client.post("/api/quality/analyses",json=body).status_code==code
    assert client.get("/api/quality/analyses").json()["total"]==1
    assert client.get("/api/quality/analyses?limit=-1").status_code==400


def test_mcp_token_does_not_expand_to_measurements_or_quality_writes(api):
    client,manager,auth=api
    project=manager.labels.create_project("token-scope",["part"])
    token=auth.issue_mcp_token("admin","quality-scope",["read","draft"],[project["id"]])
    headers={"Authorization":"Bearer "+token["token"]}
    assert client.get("/api/quality/analyses",headers=headers).status_code==403
    assert client.post("/api/quality/analyses",json=spc(),headers=headers).status_code==403
