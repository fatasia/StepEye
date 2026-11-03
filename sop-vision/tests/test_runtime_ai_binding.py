from visual.ai_services import AIServiceStore, ServiceTransport
from visual.dsl import StationConfig
from visual.llm_provider import ProviderConfig
from visual.runtime import StationRuntime


def test_sop_vlm_uses_live_binding_without_environment_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(ProviderConfig, 'from_env', classmethod(lambda cls, *a: cls(api_key='')))
    cfg = StationConfig.model_validate({
        'station': 'sop', 'source': {'type': 'sim'}, 'zones': [], 'hands': {'enabled': False},
        'cycle': {'start': {'signal': {'name': 'start'}}, 'end': {'signal': {'name': 'end'}}},
        'steps': [{'id': 'S1', 'evidence': {'all': [{'vlm_check': {'prompt': 'part present'}}]}}]})
    store = AIServiceStore(tmp_path / 'ai.json')
    rt = StationRuntime(cfg, tmp_path / 'runtime')
    rt.bind_ai_services(store)
    calls = []
    def reply(self, url, headers, payload):
        calls.append(payload['model'])
        return {'choices': [{'message': {'content': '{"verdict":"yes","confidence":0.9,"reason":"present"}'}}]}
    monkeypatch.setattr(ServiceTransport, 'post', reply)
    try:
        assert not rt._vlm_provider.ready and rt._vlm_thread is None
        store.update('v', {'name': 'VLM', 'model': 'first', 'base_url': 'http://127.0.0.1:1234/v1'})
        store.bind({'chat': None, 'vlm': 'v', 'image': None})
        for model in ['first', 'second']:
            store.update('v', {'name': 'VLM', 'model': model, 'base_url': 'http://127.0.0.1:1234/v1'})
            assert rt._vlm_provider.ready
            rt._vlm_q.put((rt._vision_generation, 'check', 'part present', b'jpeg-test-fixture'))
            rt._vlm_worker()
            assert rt._vlm_results.pop()[2] is True
        assert calls == ['first', 'second']
    finally:
        rt.close()
