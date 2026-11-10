"""Accuracy watch runs only when a reviewed report is committed."""
import copy
import sqlite3

import pytest
from fastapi.testclient import TestClient
from visual.holdout_routes import HoldoutEvaluations
from visual.server import build_app
from visual.auth import AuthStore
from test_accuracy_monitor import report


def row(name, detected=30, **changes):
    return report(name, detected) | {'engine_id':'engine-a', 'project_id':'project-a',
                                    'prediction_protocol':{'score_floor':.001}} | changes


def test_new_report_is_compared_and_restart_preserves_observation(tmp_path):
    path=tmp_path/'reports.db';store=HoldoutEvaluations(path)
    baseline=row('base');store.create(baseline)
    store.configure_watch('HE-base', {'enabled':True}, 'operator')
    store.create(row('current',15))
    result=store.get('HE-current')['accuracy_watch']
    assert result['status']=='observed_drop' and result['automatic_rollback'] is False
    assert store.get('HE-base')==baseline
    assert next(r for r in store.list()['items'] if r['id']=='HE-current')['accuracy_watch_status']=='observed_drop'
    with pytest.raises(sqlite3.IntegrityError):
        store._db.execute('DELETE FROM accuracy_observations')
    store.close();store=HoldoutEvaluations(path)
    assert store.get('HE-current')['accuracy_watch']==result
    assert store.watch_status('HE-current')['config']['baseline_id']=='HE-base'
    store.close()


@pytest.mark.parametrize('changes',[{'project_id':'another'}, {'engine_id':'another'},
    {'model_sha256':'b'*64}, {'adapter':'yolo_e2e'}, {'prediction_protocol':{'score_floor':.1}}])
def test_different_scope_never_uses_baseline(tmp_path,changes):
    store=HoldoutEvaluations(tmp_path/'reports.db')
    try:
        store.create(row('base'));store.configure_watch('HE-base',{},'operator')
        store.create(row('other',0,**changes))
        assert 'accuracy_watch' not in store.get('HE-other')
    finally:store.close()


def test_disabled_and_changed_baseline_keep_old_evidence(tmp_path):
    store=HoldoutEvaluations(tmp_path/'reports.db')
    try:
        store.create(row('base'));store.configure_watch('HE-base',{},'operator')
        store.create(row('current',15));old=copy.deepcopy(store.get('HE-current'))
        store.configure_watch('HE-current',{'enabled':False},'operator')
        store.create(row('disabled',0));assert 'accuracy_watch' not in store.get('HE-disabled')
        store.configure_watch('HE-current',{'enabled':True},'operator')
        store.create(row('new',15))
        assert store.get('HE-new')['accuracy_watch']['baseline_id']=='HE-current'
        assert store.get('HE-current')==old
    finally:store.close()


def test_invalid_report_rolls_back_insert_and_does_not_poison_connection(tmp_path):
    store=HoldoutEvaluations(tmp_path/'reports.db')
    try:
        store.create(row('base'));store.configure_watch('HE-base',{},'operator')
        broken=row('bad');broken['frames'][0]['reviewed']=False
        with pytest.raises(ValueError):store.create(broken)
        with pytest.raises(KeyError):store.get('HE-bad')
        store.create(row('good'));assert store.get('HE-good')['accuracy_watch']['status']=='no_observed_drop'
    finally:store.close()


def test_api_permissions_and_roundtrip(tmp_path):
    auth=AuthStore(tmp_path/'auth.db')
    for role in ('admin','operator','viewer'):auth.set_user(role,'test-password',role)
    auth.set_user('scoped','test-password','operator',project_ids=[])
    app=build_app([],tmp_path/'data',auth=auth);store=app.state.manager.holdout_evaluations
    store.create(row('base'));endpoint='/api/evaluations/holdout/HE-base/watch'
    try:
        with TestClient(app) as client:
            assert client.post(endpoint,json={}).status_code==401
            for username in ('viewer','scoped'):
                client.post('/api/auth/login',json={'username':username,'password':'test-password'})
                assert client.post(endpoint,json={}).status_code==403
            client.post('/api/auth/login',json={'username':'operator','password':'test-password'})
            assert client.post(endpoint,json={}).status_code==200
            assert client.get(endpoint).json()['config']['enabled'] is True
            assert client.post(endpoint,json={'min_drop':0}).status_code==422
            assert client.post(endpoint,json={'min_samples':True}).status_code==422
            assert client.post('/api/evaluations/holdout/HE-missing/watch',json={}).status_code==404
    finally:auth.close()
