"""可配置的多模态大模型 Provider —— 拍照即检路线(V 系列)的推理入口。

设计约束(ADR-002):
- API 优先、Provider 抽象:换模型 / 换端点只改配置,不改业务代码。
- 凭据只从环境变量或 .env 读取(.env 在 gitignore 内),不落代码、不写日志。
- 输出必须是结构化 JSON;解析失败按业务失败计,不猜测、不重试解析。

配置优先级:环境变量 > .env 文件 > 代码默认值。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .local_vlm import LocalVLMConfig

# 瞬时失败:值得退避重试;其余 4xx 是请求本身的问题,重试无意义
_TRANSIENT_CODES = frozenset({429, 500, 502, 503, 504})


def load_env_file(path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 行的 .env;空行与 # 注释忽略,值两侧引号剥掉。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _default_env_path() -> Path:
    """仓库根(visual/ 的上一级)优先,其次当前目录。"""
    repo_root = Path(__file__).resolve().parent.parent
    for cand in (repo_root / ".env", Path.cwd() / ".env"):
        if cand.is_file():
            return cand
    return repo_root / ".env"


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str = "https://open.bigmodel.cn/api/coding/paas/v4"
    model: str = "glm-5.3-flash"
    api_key: str = ""
    timeout_s: float = 120.0
    max_retries: int = 2
    temperature: float = 0.1  # 判定任务锁低温:同一张图反复判不能在 NG/待复核之间跳
    max_tokens: int | None = None  # 本地短判定可设256；旧云端配置保持不变
    local_vlm: LocalVLMConfig | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.local_vlm)

    def __post_init__(self):
        if not math.isfinite(self.timeout_s) or not 0 < self.timeout_s <= 600:
            raise ValueError("ZHIPU_TIMEOUT 须为0–600秒之间的正数")
        if not 0 <= self.max_retries <= 3:
            raise ValueError("ZHIPU_MAX_RETRIES 须为0–3")
        if self.max_tokens is not None and not 16 <= self.max_tokens <= 8192:
            raise ValueError("ZHIPU_MAX_TOKENS 须为16–8192")

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> ProviderConfig:
        file_vals = load_env_file(Path(env_file) if env_file else _default_env_path())

        def get(key: str, default: str = "") -> str:
            return os.environ.get(key) or file_vals.get(key, default)

        from .local_vlm import LocalVLMConfig
        local = LocalVLMConfig.from_values({**file_vals, **os.environ})
        return cls(
            base_url=local.base_url if local else get("ZHIPU_BASE", cls.base_url),
            model=local.model_alias if local else get("ZHIPU_MODEL", cls.model),
            api_key="" if local else get("ZHIPU_API_KEY"),
            timeout_s=local.request_timeout_s if local else float(get("ZHIPU_TIMEOUT", "120") or 120),
            max_retries=0 if local else int(get("ZHIPU_MAX_RETRIES", "2")),
            max_tokens=(int(get("ZHIPU_MAX_TOKENS", "256")) if local else
                        int(get("ZHIPU_MAX_TOKENS")) if get("ZHIPU_MAX_TOKENS") else None),
            local_vlm=local,
        )


class Transport:
    """HTTP 传输,独立出来便于测试注入假传输。"""

    def __init__(self, timeout_s: float = 120.0):
        self.timeout_s = timeout_s

    def post(self, url: str, headers: dict[str, str], payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.load(resp)


class VLMProvider:
    """把「图片 + 判定提示词」变成结构化 JSON 结论。"""

    def __init__(self, config: ProviderConfig, transport: Transport | None = None):
        self.cfg = config
        if transport is None and config.local_vlm is not None:
            from .local_vlm import get_transport
            transport = get_transport(config.local_vlm)
        self.transport = transport or Transport(config.timeout_s)

    def chat(self, messages: list[dict]) -> str:
        """单轮对话,返回首个回复的文本;瞬时失败退避重试,4xx 直接抛出。"""
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        payload = {"model": self.cfg.model, "messages": messages, "temperature": self.cfg.temperature}
        if self.cfg.max_tokens is not None:
            payload["max_tokens"] = self.cfg.max_tokens
        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                resp = self.transport.post(url, headers, payload)
                choice = resp["choices"][0]
                # 截断JSON可能仍能解析为部分结论，不得拿它当完整质检结果。
                if choice.get("finish_reason") == "length":
                    raise ValueError("模型输出达到长度上限，请缩短提示词或增加输出额度")
                content = choice["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("模型返回的文本格式不正确")
                return content
            except Exception as err:  # noqa: BLE001 —— 下面按类型分流,不静默吞
                if not isinstance(err, (urllib.error.URLError, TimeoutError, ConnectionError)):
                    raise
                if isinstance(err, urllib.error.HTTPError) and err.code not in _TRANSIENT_CODES:
                    raise
                last_err = err
                if attempt < self.cfg.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"大模型调用失败(已重试 {self.cfg.max_retries} 次): {last_err!r}") from last_err

    @staticmethod
    def image_message(images_b64: list[str], prompt: str) -> list[dict]:
        """多图 + 文本的用户消息;空图列表退化为纯文本,让调用方不必特判。"""
        content: list[dict] = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            for b64 in images_b64
        ]
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    def inspect(self, images_b64: list[str], prompt: str) -> dict[str, Any]:
        """拍照即检的判定入口:调用失败或 JSON 不合法都算一次失败的判定,不抛异常。"""
        try:
            text = self.chat(self.image_message(images_b64, prompt))
        except Exception as err:  # noqa: BLE001 —— 判定失败是业务结果,由调用方决定降级/转人工
            return {"ok": False, "verdict": None, "confidence": None,
                    "reason": f"调用失败: {err}", "raw": None}
        data = extract_json(text)
        if data is None:
            return {"ok": False, "verdict": None, "confidence": None,
                    "reason": "输出不是合法 JSON", "raw": text}
        return {"ok": True, "verdict": data.get("verdict"),
                "confidence": data.get("confidence"), "reason": data.get("reason", ""),
                "raw": text}


def extract_json(text: str) -> dict | None:
    """从回复中取第一个 JSON 对象;容忍 markdown 围栏与前后解释文字。"""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
