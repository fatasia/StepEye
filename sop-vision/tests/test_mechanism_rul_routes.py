"""RUL route contract without requiring downloadable model assets."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from visual.mechanism_routes import MechanismReports, router
from visual.mechanism_rul_routes import rul_router
from visual.mechanism_rul import CHANNELS, RULError

class Service:
    error = None
    def capabilities(self):
        return {'available': True, 'dataset': 'NASA-CMAPSS-FD001'}
    def analyze(self, body):
        if self.error:
            raise self.error
        return {'status': 'evidence_only', 'results': [], 'kind': 'cmapss_rul'}

def test_rul_route_report_and_error_status(tmp_path):
    store = MechanismReports(tmp_path / 'reports.db')
    service = Service()
    app = FastAPI()
    app.include_router(router(store))
    app.include_router(rul_router(store, service))
    payload = {'dataset':'NASA-CMAPSS-FD001', 'channels':CHANNELS,
               'windows':[{'unit_id':'contract-test', 'end_cycle':30,
                           'values':[[0.] * 24 for _ in range(30)]}]}
    try:
        with TestClient(app) as client:
            assert client.get('/api/mechanism/rul/capabilities').json()['available']
            response = client.post('/api/mechanism/rul/analyses', json=payload)
            assert response.status_code == 201
            report = response.json()
            assert report['kind'] == 'cmapss_rul'
            assert client.get(f"/api/mechanism/analyses/{report['id']}/export").json() == report
            assert client.post('/api/mechanism/rul/analyses', json={}).status_code == 422
            for status in [409, 429, 503, 504]:
                service.error = RULError('test failure', status)
                assert client.post('/api/mechanism/rul/analyses', json=payload).status_code == status
            assert store.list()['total'] == 1
    finally:
        store.close()
