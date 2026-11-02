from __future__ import annotations

import urllib.error

from visual.llm_provider import ProviderConfig, VLMProvider, extract_json, load_env_file


class FakeTransport:
    """假传输:按脚本回放响应或抛错,记录每次调用,全程不联网。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def post(self, url, headers, payload):
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def _cfg() -> ProviderConfig:
    return ProviderConfig(api_key="k-test", max_retries=1)


def test_load_env_file_parses_and_strips_quotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        'ZHIPU_API_KEY="abc"\n\n# 注释行\nZHIPU_MODEL=glm-x\nBAD_LINE_NO_EQUALS\n',
        encoding="utf-8",
    )
    vals = load_env_file(env)
    assert vals == {"ZHIPU_API_KEY": "abc", "ZHIPU_MODEL": "glm-x"}


def test_load_env_file_missing_is_empty(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == {}


def test_config_env_vars_beat_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ZHIPU_API_KEY=file-key\nZHIPU_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("ZHIPU_API_KEY", "env-key")
    monkeypatch.delenv("ZHIPU_MODEL", raising=False)
    cfg = ProviderConfig.from_env(tmp_path / ".env")
    assert cfg.api_key == "env-key"  # 环境变量优先
    assert cfg.model == "file-model"  # 文件兜底


def test_config_without_key_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    assert ProviderConfig.from_env(tmp_path / "nope.env").api_key == ""


def test_image_message_multimodal_and_text_fallback():
    msgs = VLMProvider.image_message(["QUJD"], "判定螺丝")
    content = msgs[0]["content"]
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
    assert content[-1] == {"type": "text", "text": "判定螺丝"}
    assert VLMProvider.image_message([], "纯文本")[0]["content"] == [{"type": "text", "text": "纯文本"}]


def test_chat_sends_auth_header_and_model():
    t = FakeTransport([_resp("ok")])
    p = VLMProvider(_cfg(), t)
    assert p.chat([{"role": "user", "content": "hi"}]) == "ok"
    call = t.calls[0]
    assert call["headers"]["Authorization"] == "Bearer k-test"
    assert call["payload"]["model"] == "glm-5.3-flash"
    assert call["payload"]["temperature"] == 0.1  # 判定任务锁低温
    assert call["url"].endswith("/chat/completions")


def test_inspect_parses_fenced_json():
    t = FakeTransport([_resp('```json\n{"verdict": "NG", "confidence": 0.9, "reason": "缺件"}\n```')])
    out = VLMProvider(_cfg(), t).inspect(["QUJD"], "p")
    assert out["ok"] and out["verdict"] == "NG" and out["confidence"] == 0.9


def test_inspect_retries_transient_then_succeeds():
    err = urllib.error.HTTPError("u", 429, "rate", None, None)
    t = FakeTransport([err, _resp('{"verdict": "PASS"}')])
    out = VLMProvider(_cfg(), t).inspect([], "p")
    assert out["ok"] and out["verdict"] == "PASS"
    assert len(t.calls) == 2  # 瞬时失败重试了 1 次


def test_inspect_no_retry_on_request_error():
    t = FakeTransport([urllib.error.HTTPError("u", 400, "bad request", None, None)])
    out = VLMProvider(_cfg(), t).inspect([], "p")
    assert not out["ok"] and "调用失败" in out["reason"]
    assert len(t.calls) == 1  # 400 是请求问题,不重试


def test_inspect_marks_non_json_as_failed():
    t = FakeTransport([_resp("我觉得没问题")])
    out = VLMProvider(_cfg(), t).inspect([], "p")
    assert not out["ok"] and out["reason"] == "输出不是合法 JSON"
    assert out["raw"] == "我觉得没问题"  # 原文保留供复核


def test_extract_json_tolerates_surrounding_text():
    assert extract_json('结论如下:{"verdict": "PASS"} 请查收') == {"verdict": "PASS"}
    assert extract_json("没有任何大括号") is None
    assert extract_json("{不完整") is None
    assert extract_json('[1, 2]') is None  # 数组不算判定对象
