"""盘点账实核对:导入/版本、对账差异、不可变报告、路由权限。范围=单机位可见计数对账。"""
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from visual.stock_reconciliation import ExpectedImportBody, ReconcileBody, StockReconStore
from visual.stock_reconciliation_routes import router


@pytest.fixture
def store(tmp_path):
    s = StockReconStore(tmp_path / 'stock_recon.sqlite')
    yield s
    s.close()


def make_client(role):
    def build(s):
        app = FastAPI()
        app.include_router(router(s))

        @app.middleware('http')
        def fake_user(request, call_next):
            request.state.user = None if role is None else {'username': 't', 'role': role, 'project_ids': []}
            return call_next(request)
        return TestClient(app)
    return build


def imp(station='st1', items=None):
    return {'station': station, 'items': items if items is not None else
            [{'zone': 'z1', 'item': 'box', 'count': 5}, {'zone': 'z1', 'item': 'rack', 'count': 2},
             {'zone': 'z2', 'item': 'box', 'count': 1}]}


def rec(observed=None, station='st1'):
    return {'station': station, 'observed': observed or []}


# ---- store 核心:导入与版本历史 ----
def test_import_replaces_and_keeps_version_history(store):
    first = store.import_expected(imp(), 'op1')
    assert first['version'] == 1 and first['actor'] == 'op1'
    second = store.import_expected(imp(items=[{'zone': 'z1', 'item': 'box', 'count': 7}]), 'op2')
    assert second['version'] == 2
    current = store.get_expected('st1')
    assert current['items'] == [{'zone': 'z1', 'item': 'box', 'count': 7}]
    versions = store.versions('st1')
    assert [v['version'] for v in versions] == [2, 1]  # 新→旧
    with pytest.raises(KeyError):
        store.versions('nope')


def test_import_rejects_duplicate_zone_item_and_empty(store):
    with pytest.raises(ValueError):
        ExpectedImportBody(**imp(items=[{'zone': 'z1', 'item': 'box', 'count': 1},
                                        {'zone': 'z1', 'item': 'box', 'count': 2}]))
    with pytest.raises(ValueError):
        ExpectedImportBody(station='st1', items=[])


# ---- store 核心:对账差异 ----
def test_reconcile_reports_all_difference_kinds(store):
    store.import_expected(imp(), 'op')
    report = store.reconcile(rec(observed=[{'zone': 'z1', 'item': 'box', 'count': 3},   # 5→3 缺
                                           {'zone': 'z1', 'item': 'rack', 'count': 2},  # 一致
                                           {'zone': 'z2', 'item': 'box', 'count': 4},   # 1→4 多
                                           {'zone': 'z9', 'item': 'tool', 'count': 7}]), 'op')  # 账外
    assert report['id'].startswith('SR-') and report['expected_version'] == 1
    assert '跨机位去重未实现' in report['scope']
    assert report['summary'] == {'match': 1, 'missing': 1, 'surplus': 1, 'unobserved': 0, 'unexpected': 1}
    rows = {(r['zone'], r['item']): r for r in report['rows']}
    assert rows[('z1', 'box')]['status'] == 'missing' and rows[('z1', 'box')]['observed_count'] == 3
    assert rows[('z2', 'box')]['status'] == 'surplus'
    assert rows[('z9', 'tool')]['status'] == 'unexpected' and rows[('z9', 'tool')]['count'] is None
    later = store.reconcile(rec(), 'op')  # 无观测:期望项全部不可判定,不猜缺
    assert later['summary']['unobserved'] == 3 and later['summary']['missing'] == 0


def test_reconcile_requires_expected_and_unique_observed(store):
    with pytest.raises(KeyError):
        store.reconcile(rec(), 'op')
    store.import_expected(imp(), 'op')
    with pytest.raises(ValueError):
        store.reconcile(rec(observed=[{'zone': 'z1', 'item': 'box', 'count': 1},
                                      {'zone': 'z1', 'item': 'box', 'count': 2}]), 'op')


# ---- 不可变报告 ----
def test_reports_are_immutable_at_sql_level(store):
    store.import_expected(imp(), 'op')
    report = store.reconcile(rec(), 'op')
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("UPDATE recon_reports SET body='tampered'")
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute("DELETE FROM recon_reports")
    assert store.get_report(report['id'])['id'] == report['id']
    listed = store.list_reports(station='st1')
    assert listed['total'] == 1 and listed['reports'][0]['id'] == report['id']
    assert store.list_reports(station='other')['total'] == 0


# ---- 路由与权限 ----
def test_routes_auth_and_role_matrix(store):
    anon, viewer, operator = make_client(None)(store), make_client('viewer')(store), make_client('operator')(store)
    assert anon.get('/api/stock/expected').status_code == 401
    assert anon.post('/api/stock/reconcile', json=rec()).status_code == 401
    assert viewer.post('/api/stock/expected', json=imp()).status_code == 403
    assert operator.post('/api/stock/expected', json=imp()).status_code == 201
    assert viewer.get('/api/stock/expected/st1').status_code == 200
    assert viewer.post('/api/stock/reconcile', json=rec()).status_code == 403
    assert operator.post('/api/stock/reconcile', json=rec(
        observed=[{'zone': 'z1', 'item': 'box', 'count': 5}])).status_code == 201
    data = viewer.get('/api/stock/reconciliations?station=st1').json()
    assert data['total'] == 1 and data['reports'][0]['summary']['match'] == 1
    assert operator.get('/api/stock/expected').status_code == 200


def test_routes_error_mapping(store):
    client = make_client('operator')(store)
    assert client.get('/api/stock/expected/st1').status_code == 404
    assert client.post('/api/stock/reconcile', json=rec()).status_code == 404
    assert client.post('/api/stock/expected', json=imp(items=[])).status_code == 422
    assert client.post('/api/stock/expected', json=imp(
        items=[{'zone': 'z1', 'item': 'box', 'count': -1}])).status_code == 422
    assert client.post('/api/stock/expected', json=imp(
        items=[{'zone': 'z1', 'item': 'box', 'count': 1}, {'zone': 'z1', 'item': 'box', 'count': 2}])).status_code == 422
    assert client.post('/api/stock/expected', json=imp()).status_code == 201  # 此后对账才有期望值可比
    assert client.post('/api/stock/reconcile', json=rec(
        observed=[{'zone': 'z1', 'item': 'box', 'count': 1}, {'zone': 'z1', 'item': 'box', 'count': 2}])).status_code == 400
    assert client.get('/api/stock/reconciliations/none').status_code == 404


def test_reconcile_body_model_defaults():
    body = ReconcileBody(station='s')
    assert body.observed == [] and body.note == ''
