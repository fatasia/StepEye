from types import SimpleNamespace
from fastapi.testclient import TestClient
from visual.auth import AuthStore
from visual.server import build_app


def test_camera_control_requires_write_role_and_reports_queued(tmp_path, monkeypatch):
    auth = AuthStore(tmp_path / 'auth.db')
    auth.set_user('viewer', 'password123', 'viewer')
    auth.set_user('operator', 'password123', 'operator')
    app = build_app([], tmp_path / 'data', auth=auth)
    calls = []
    def trigger():
        calls.append('trigger')
        return {'queued': True}
    source = SimpleNamespace(capabilities=lambda: {'opened': False}, software_trigger=trigger)
    monkeypatch.setattr(app.state.manager, 'get', lambda sid: SimpleNamespace(source=source))
    with TestClient(app) as client:
        assert client.get('/api/stations/s/camera').status_code == 401
        client.post('/api/auth/login', json={'username':'viewer','password':'password123'})
        assert client.get('/api/stations/s/camera').json()['opened'] is False
        assert client.post('/api/stations/s/camera/trigger').status_code == 403
        assert not calls
        client.post('/api/auth/login', json={'username':'operator','password':'password123'})
        response = client.post('/api/stations/s/camera/trigger')
        assert response.status_code == 202
        assert response.json() == {'queued': True, 'frame_received': False}
        assert calls == ['trigger']
        del source.software_trigger
        assert client.post('/api/stations/s/camera/trigger').status_code == 409
    auth.close()
