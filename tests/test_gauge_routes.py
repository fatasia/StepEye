from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.gauge_routes import gauge_router
from visual.gauge import GaugeError


def test_gauge_upload_and_calibration_contract():
    class Service:
        def capabilities(self): return {'available': True}
        def trial(self, raw, calibration):
            if raw == b'error': raise GaugeError('busy', 429)
            return {'bytes':len(raw), 'calibration':calibration.model_dump() if calibration else None}
    app = FastAPI()
    app.include_router(gauge_router(Service()))
    with TestClient(app) as client:
        assert client.get('/api/gauge/capabilities').json()['available']
        response = client.post('/api/gauge/trial', files={'file':('test.jpg',b'test','image/jpeg')})
        assert response.json() == {'bytes':4,'calibration':None}
        assert client.post('/api/gauge/trial', files={'file':('test.jpg',b'test')}, data={'calibration':'{}'}).status_code == 422
        assert client.post('/api/gauge/trial', files={'file':('test.jpg',b'error')}).status_code == 429
        assert client.post('/api/gauge/trial', files={'file':('test.jpg',b'x'*(10*1024*1024+1))}).status_code == 413
