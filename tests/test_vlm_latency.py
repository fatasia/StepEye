import pytest
from visual.llm_provider import ProviderConfig, VLMProvider


class Transport:
    def __init__(self, reason="stop"):
        self.calls = []
        self.reason = reason

    def post(self, url, headers, payload):
        self.calls.append(payload)
        return {"choices": [{"finish_reason": self.reason,
                             "message": {"content": '{"verdict":"PASS"}'}}]}


def test_local_latency_config(tmp_path, monkeypatch):
    for key in ('ZHIPU_MAX_TOKENS', 'ZHIPU_MAX_RETRIES', 'ZHIPU_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / '.env'
    env.write_text('ZHIPU_MAX_TOKENS=256\nZHIPU_MAX_RETRIES=0\nZHIPU_TIMEOUT=15\n')
    cfg = ProviderConfig.from_env(env)
    assert (cfg.max_tokens, cfg.max_retries, cfg.timeout_s) == (256, 0, 15)
    transport = Transport()
    assert VLMProvider(cfg, transport).inspect([], 'p')['ok']
    assert transport.calls[0]['max_tokens'] == 256


def test_truncated_output_fails_without_retry():
    transport = Transport('length')
    assert not VLMProvider(ProviderConfig(), transport).inspect([], 'p')['ok']
    assert len(transport.calls) == 1


@pytest.mark.parametrize('kwargs', [{'max_tokens': 0}, {'max_retries': 4}, {'timeout_s': float('nan')}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        ProviderConfig(**kwargs)


def test_local_config_enables_without_cloud_key(tmp_path, monkeypatch):
    values = {'STEPEYE_LOCAL_VLM_BIN': 'tools/llama-server.exe',
              'STEPEYE_LOCAL_VLM_MODEL': 'models/qwen.gguf',
              'STEPEYE_LOCAL_VLM_MMPROJ': 'models/mmproj.gguf',
              'ZHIPU_API_KEY': 'cloud-secret-not-for-local',
              'ZHIPU_BASE': 'https://cloud.invalid/v1', 'ZHIPU_MAX_RETRIES': '3'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('ZHIPU_MAX_TOKENS', raising=False)
    cfg = ProviderConfig.from_env(tmp_path / 'missing.env')
    assert cfg.configured and not cfg.api_key
    assert cfg.base_url == 'http://127.0.0.1:18081/v1'
    assert cfg.model == 'stepeye-local'
    assert (cfg.max_tokens, cfg.max_retries, cfg.timeout_s) == (256, 0, 15)
    transport = Transport()
    assert VLMProvider(cfg, transport).inspect([], 'p')['ok']
