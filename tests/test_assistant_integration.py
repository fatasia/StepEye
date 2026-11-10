"""Real app authorization and document retrieval, without an external model."""
from fastapi.testclient import TestClient
from visual.auth import AuthStore
from visual.server import build_app


def test_documents_chat_and_service_permissions(tmp_path):
    auth = AuthStore(tmp_path / 'auth.db')
    for role in ('admin', 'operator', 'viewer'):
        auth.set_user(role, 'TestPassword123', role)
    app = build_app([], tmp_path / 'data', auth=auth)
    with TestClient(app) as client:
        def login(role):
            assert client.post('/api/auth/login', json={
                'username': role, 'password': 'TestPassword123'}).status_code == 200

        assert client.post('/api/assistant/chat', json={'message': '安全操作'}).status_code == 401
        login('operator')
        assert client.get('/api/ai/services').status_code == 403
        upload = client.post('/api/documents', data={'title': '安全操作手册'},
            files={'file': ('manual.md', '安全操作：切断电源后检查设备。'.encode(), 'text/markdown')})
        assert upload.status_code == 201, upload.text
        vid = upload.json()['id']
        login('viewer')
        response = client.post('/api/assistant/chat', json={'message': '安全操作'})
        assert response.status_code == 200, response.text
        assert response.json()['sources'] == []
        assert client.put(f'/api/documents/versions/{vid}/status', json={'status': 'published'}).status_code == 403
        login('admin')
        assert client.get('/api/ai/services').status_code == 200
        assert client.put(f'/api/documents/versions/{vid}/status', json={'status': 'published'}).status_code == 200
        login('viewer')
        reply = client.post('/api/assistant/chat', json={'message': '安全操作'}).json()
        assert reply['mode'] == 'retrieval'
        assert reply['sources'][0]['version_id'] == vid
        assert '切断电源' in reply['answer']
        assert client.post('/api/assistant/chat', json={
            'message': 'x', 'history': [{'role': 'system', 'content': 'x'}]}).status_code == 422
    auth.close()


def test_vlm_binding_activates_existing_scenarios_without_restart(tmp_path):
    from visual.llm_provider import ProviderConfig
    config = tmp_path / 'inspection.yaml'
    config.write_text('scenarios:\n  - id: part\n    name: Part\n    prompt: Check part\n', encoding='utf-8')
    app = build_app([], tmp_path / 'data', inspection_config=config,
                    provider_cfg=ProviderConfig(api_key=''))
    with TestClient(app) as client:
        assert client.get('/api/inspection/scenarios').status_code == 503
        response = client.put('/api/ai/services/local', json={
            'name': 'Local', 'model': 'vision', 'base_url': 'http://127.0.0.1:9999/v1'})
        assert response.status_code == 200
        assert client.put('/api/ai/bindings', json={'chat': None, 'vlm': 'local', 'image': None}).status_code == 200
        assert client.get('/api/inspection/scenarios').status_code == 200
        assert app.state.manager.inspection._thread is None
