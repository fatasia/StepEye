"""动作对账账本：记录、窗口去重、聚合一致率与 runtime 对账挂点。"""
import pytest

from visual.action_ledger import ActionLedger
from visual.dsl import ActionShadowCfg
from visual.action_ledger_routes import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def ledger(tmp_path):
    store = ActionLedger(tmp_path / 'ledger.db')
    yield store
    store.close()


def rec(ledger, station='s1', window=1, observed='tighten_nut', expected=('tighten_nut',),
        cycle='c1', conf=.8):
    return ledger.record(station, cycle, ['fasten'], list(expected), observed,
                         conf, 'a' * 64, window)


def test_record_and_dedupe_by_window(ledger):
    first = rec(ledger, window=1)
    assert first is not None and first['agree'] is True
    # 同一窗口重复记录被吞：返回 None 且不新增行。
    assert rec(ledger, window=1, observed='fit_nut') is None
    assert len(ledger.list()) == 1


def test_unmapped_step_records_null_agree(ledger):
    row = rec(ledger, window=2, expected=())
    assert row['agree'] is None
    assert ledger.summary()[0]['unmapped'] == 1 and ledger.summary()[0]['judged'] == 0


def test_summary_agreement_rate(ledger):
    rec(ledger, window=1, observed='tighten_nut', expected=('tighten_nut',))
    rec(ledger, window=2, observed='fit_nut', expected=('tighten_nut',))
    rec(ledger, window=3, observed='align_objects', expected=('align_objects', 'fit_nut'))
    s = ledger.summary(station='s1')[0]
    assert s['judged'] == 3 and s['agreed'] == 2 and s['disagreed'] == 1
    assert s['agreement_rate'] == round(2 / 3, 4)
    assert ledger.summary(station='other') == []


def test_list_filters_by_station_and_step(ledger):
    rec(ledger, station='s1', window=1, cycle='c1')
    rec(ledger, station='s2', window=1, cycle='c1')
    assert len(ledger.list(station='s1')) == 1
    # step 过滤基于记录的步骤列表
    rec(ledger, station='s1', window=2, cycle='c1')
    rows = ledger.list(station='s1', step='fasten')
    assert len(rows) == 2 and all('fasten' in r['step_ids'] for r in rows)


def test_invalid_observation_rejected(ledger):
    with pytest.raises(ValueError):
        rec(ledger, observed='', window=9)
    with pytest.raises(ValueError):
        rec(ledger, conf=1.5, window=9)


def test_step_actions_cfg_validation():
    cfg = ActionShadowCfg(enabled=False, model='', class_names=['tighten_nut'],
                          step_actions={'fasten': ['tighten_nut']})
    assert cfg.step_actions == {'fasten': ['tighten_nut']}
    with pytest.raises(Exception):
        ActionShadowCfg(enabled=False, model='', class_names=['tighten_nut'],
                        step_actions={'fasten': ['fit_nut']})
    with pytest.raises(Exception):
        ActionShadowCfg(enabled=False, model='', class_names=['tighten_nut'],
                        step_actions={'': ['tighten_nut']})


@pytest.fixture
def client(tmp_path):
    store = ActionLedger(tmp_path / 'ledger.db')
    app = FastAPI()
    app.include_router(router(store))

    async def fake_user(request, call_next):
        request.state.user = {'username': 'op', 'role': 'viewer'}
        return await call_next(request)

    app.middleware('http')(fake_user)
    yield TestClient(app), store
    store.close()


def test_http_list_and_summary(client):
    http, store = client
    store.record('s1', 'c1', ['fasten'], ['tighten_nut'], 'tighten_nut', .9, 'a' * 64, 1)
    resp = http.get('/api/action/ledger', params={'station': 's1'})
    assert resp.status_code == 200 and len(resp.json()['items']) == 1
    resp = http.get('/api/action/ledger/summary')
    assert resp.status_code == 200
    assert resp.json()['items'][0]['agreement_rate'] == 1.0


def test_runtime_reconcile_records_and_dedupes(tmp_path):
    """模拟 runtime 挂点：影子 ready 结果记一条，重复窗口不重复记。"""
    ledger = ActionLedger(tmp_path / 'ledger.db')
    cfg = ActionShadowCfg(enabled=False, model='', class_names=['tighten_nut', 'fit_nut'],
                          step_actions={'fasten': ['tighten_nut']})

    class FakeShadow:
        def __init__(self):
            self.n = 0

        def snapshot(self):
            self.n += 1
            if self.n == 1:
                return {'result': {'window_id': 7, 'label': 'tighten_nut', 'confidence': .9,
                                   'model_sha256': 'a' * 64, 'cycle_id': 'c1'}}
            return {'result': None}

    class RTConfig:
        station = 's1'

    class FakeCycle:
        id = 'c1'
        steps = []

    from visual.runtime import StationRuntime
    import inspect
    sig = inspect.signature(StationRuntime.__init__)
    assert 'action_ledger' in sig.parameters

    shadow = FakeShadow()

    # 直接调用 runtime 的对账方法（避免起真实采集线程）
    rt = StationRuntime.__new__(StationRuntime)
    rt.cfg = RTConfig()
    rt.action_ledger = ledger
    rt._ledger_window = None
    rt.action_shadow = shadow
    rt._action_reconcile(cfg, FakeCycle(), {'fasten'}, 1.0)
    rows = ledger.list(station='s1')
    assert len(rows) == 1 and rows[0]['agree'] is True and rows[0]['window_id'] == 7
    # 同窗口再次调用（影子无新结果）不新增
    rt._action_reconcile(cfg, FakeCycle(), {'fasten'}, 1.0)
    assert len(ledger.list()) == 1
    ledger.close()


def test_equipment_evidence_roundtrip(ledger):
    evidence = {"signals": [{"name": "torque_ok", "value": True, "source": "plc",
                             "quality": "ok", "observed_at": 1760000000.0}]}
    row = ledger.record('s1', 'c1', ['fasten'], ['tighten_nut'], 'tighten_nut',
                        .9, 'a' * 64, 7, evidence=evidence)
    assert row['evidence'] == evidence
    stored = ledger.list(station='s1')[0]
    assert stored['evidence']['signals'][0]['name'] == 'torque_ok'


def test_equipment_evidence_size_limited(ledger):
    huge = {"signals": [{"name": "x" * 9000, "value": 1}]}
    with pytest.raises(ValueError, match='设备证据过大'):
        ledger.record('s1', 'c1', ['fasten'], [], 'fit_nut', .8, 'a' * 64, 8, evidence=huge)


def test_old_ledger_schema_migrates_evidence_column(tmp_path):
    import sqlite3
    path = tmp_path / 'old.db'
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE action_reconciliation (
        id TEXT PRIMARY KEY, station TEXT, cycle_id TEXT, step_ids TEXT,
        expected TEXT, observed TEXT, confidence REAL, agree INTEGER,
        model_sha256 TEXT, window_id INTEGER, at TEXT, UNIQUE(station, window_id))""")
    db.execute("INSERT INTO action_reconciliation VALUES('old','s1','c1','[]','[]','fit_nut',.8,0,'x',1,'t')")
    db.commit(); db.close()
    store = ActionLedger(path)
    try:
        old = store.list()[0]
        assert old['observed'] == 'fit_nut' and old['evidence'] is None
        row = store.record('s1', 'c2', ['fasten'], [], 'fit_nut', .8, 'a' * 64, 2,
                           evidence={"signals": []})
        assert row is not None and row['evidence'] == {"signals": []}
    finally:
        store.close()
