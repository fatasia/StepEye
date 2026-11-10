"""Browser project scopes use real sessions, routes and WS; capture workers are disabled."""
import io
import json
import sqlite3

from PIL import Image
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from visual.auth import AuthStore
from visual.data_scope import scope_ids
from visual.runtime import StationRuntime
from visual.server import build_app


@pytest.fixture
def app_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(StationRuntime, 'run', lambda self: None)
    auth = AuthStore(tmp_path / 'auth.db')
    auth.set_user('admin', 'password123', 'admin')
    configs = []
    for sid in ['a', 'b']:
        path = tmp_path / f'{sid}.json'
        path.write_text(json.dumps({'station': sid, 'source': {'type': 'sim'},
            'hands': {'enabled': False}, 'persons': {'enabled': False},
            'zones': [{'id': 'z', 'polygon': [[0, 0], [1, 0], [1, 1], [0, 1]]}],
            'safety': {'enabled': False, 'rules': [{'id': 'off', 'kind': 'person_absent', 'zone': 'z'}]}}))
        configs.append(path)
    app = build_app(configs, tmp_path / 'data', auth=auth)
    m = app.state.manager
    projects = [m.labels.create_project(sid, ['part'], sid) for sid in ['a', 'b']]
    raw = io.BytesIO(); Image.new('RGB', (12, 12), 'white').save(raw, 'PNG')
    assets = [m.labels.add_asset(p['id'], raw.getvalue(), 'test.png') for p in projects]
    auth.set_user('op', 'password123', 'operator', [projects[0]['id']])
    auth.set_user('viewer', 'password123', 'viewer', [projects[0]['id']])
    auth.set_user('empty', 'password123', 'viewer', [])
    alerts = [m.alerts.create(sid, 'sop', 'test', 'warning', sid)[0] for sid in ['a', 'b']]
    with TestClient(app) as client:
        def login(name):
            response = client.post('/api/auth/login', json={'username': name, 'password': 'password123'})
            assert response.status_code == 200
        yield client, auth, m, projects, assets, alerts, login
    auth.close()


def test_project_station_alert_lists_and_reads(app_scope):
    client, _, _, projects, assets, alerts, login = app_scope
    login('op')
    assert [p['id'] for p in client.get('/api/label-projects').json()] == [projects[0]['id']]
    assert [s['meta']['id'] for s in client.get('/api/stations').json()] == ['a']
    # Filtering happens before LIMIT, so the newest forbidden row cannot hide an allowed row.
    assert [a['id'] for a in client.get('/api/alerts?limit=1').json()] == [alerts[0]['id']]
    assert client.get('/api/alerts/stats').json()['total'] == 1
    assert [r['station'] for r in client.get('/api/alerts/false-positives').json()['rules']] == ['a']
    assert client.get('/api/stations/a/state').status_code == 200
    assert client.get('/api/stations/b/state').status_code == 403
    assert client.get(f"/api/label-assets/{assets[0]['id']}/image").status_code == 200
    assert client.get(f"/api/label-assets/{assets[1]['id']}/image").status_code == 403
    assert client.get(f"/api/label-projects/{projects[1]['id']}/assets").status_code == 403
    assert client.get('/api/inspection/tasks').status_code == 403


def test_cross_project_writes_and_role_checks(app_scope):
    client, _, _, projects, assets, alerts, login = app_scope
    login('op')
    body = {'expected_revision': 0, 'targets': []}
    assert client.put(f"/api/label-assets/{assets[1]['id']}/annotations", json=body).status_code == 403
    assert client.patch(f"/api/alerts/{alerts[1]['id']}", json={'status': 'done'}).status_code == 403
    assert client.patch(f"/api/alerts/{alerts[0]['id']}", json={'status': 'done'}).status_code == 200
    assert client.post('/api/label-jobs', json={'project_id': projects[1]['id']}).status_code == 403
    assert client.post('/api/label-projects', json={'name': 'outside', 'classes': ['part']}).status_code == 403
    # Allowed destination project does not grant access to global timeline/sample images.
    assert client.post(f"/api/label-projects/{projects[0]['id']}/import-evidence", json={}).status_code == 403
    login('viewer')
    assert client.patch(f"/api/alerts/{alerts[0]['id']}", json={'status': 'done'}).status_code == 403


def test_empty_scope_and_admin_global(app_scope):
    client, _, _, _, _, _, login = app_scope
    login('empty')
    assert client.get('/api/auth/me').json()['project_ids'] == []
    for path in ['/api/label-projects', '/api/stations', '/api/alerts']:
        assert client.get(path).json() == []
    assert client.get('/api/alerts/stats').json()['total'] == 0
    login('admin')
    assert len(client.get('/api/stations').json()) == 2
    assert client.patch('/api/users/admin/scope', json={'project_ids': []}).status_code == 400


def test_scope_change_revokes_session_and_password_reset_preserves_scope(app_scope):
    client, auth, _, projects, _, _, login = app_scope
    token = auth.login('op', 'password123')
    login('admin')
    assert client.post('/api/users', json={'username': 'op', 'password': 'password123', 'role': 'operator'}).status_code == 200
    assert auth.verify_session(token) is None
    token = auth.login('op', 'password123')
    assert auth.verify_session(token)['project_ids'] == [projects[0]['id']]
    assert client.patch('/api/users/op/scope', json={'project_ids': []}).status_code == 200
    assert auth.verify_session(token) is None
    assert client.patch('/api/users/op/scope', json={'project_ids': None}).status_code == 200
    assert auth.verify_session(auth.login('op', 'password123'))['project_ids'] is None


def test_websocket_filters_and_stops_on_scope_revocation(app_scope):
    client, auth, m, _, _, _, login = app_scope
    login('op')
    with client.websocket_connect('/ws') as ws:
        assert [s['id'] for s in ws.receive_json()['stations']] == ['a']
        m.hub.publish({'type': 'test', 'station': 'b', 'secret': True})
        m.hub.publish({'type': 'test', 'station': 'a', 'allowed': True})
        assert ws.receive_json() == {'type': 'test', 'station': 'a', 'allowed': True}
        auth.set_scope('op', [])
        m.hub.publish({'type': 'test', 'station': 'a', 'should_not_send': True})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_legacy_schema_migrates_global_and_mcp_empty_unchanged(tmp_path):
    path = tmp_path / 'old.db'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE users(username TEXT PRIMARY KEY,pw_salt BLOB,pw_hash BLOB,role TEXT,created TEXT)')
    db.execute("INSERT INTO users VALUES('legacy',NULL,NULL,'viewer','old')")
    db.commit(); db.close()
    auth = AuthStore(path)
    try:
        assert auth.list_users()[0]['project_ids'] is None
        auth.set_user('legacy', 'password123', 'viewer')
        assert scope_ids(auth.verify_session(auth.login('legacy', 'password123'))) is None
        assert scope_ids({'role': 'viewer', 'project_ids': [], 'mcp_token_id': 'legacy'}) is None
    finally:
        auth.close()
