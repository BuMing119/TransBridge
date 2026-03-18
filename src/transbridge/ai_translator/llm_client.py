"""
LLM 客户端抽象层。

提供：
- LLMClient (ABC)
- OpenAICompatibleClient
- AnthropicClient
- create_llm_client() 工厂函数
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig


class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        """发送消息并返回模型回复文本。max_tokens=0 表示不限制（由模型默认）。"""

    def cancel(self) -> None:
        """中断当前进行中的请求（关闭 HTTP 连接），并重建客户端供后续使用。"""


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str):
        import httpx
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._lock = threading.Lock()
        self._http_client = httpx.Client()
        self._client = self._make_client()

    def _make_client(self):
        from openai import OpenAI
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._http_client,
        )

    def cancel(self) -> None:
        import httpx
        with self._lock:
            old_http = self._http_client
            self._http_client = httpx.Client()
            self._client = self._make_client()
        try:
            old_http.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        with self._lock:
            client = self._client
        kwargs: dict = dict(model=self._model, messages=messages)
        if max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str):
        import httpx
        self._api_key = api_key
        self._model = model
        self._lock = threading.Lock()
        self._http_client = httpx.Client()
        self._client = self._make_client()

    def _make_client(self):
        import anthropic
        return anthropic.Anthropic(
            api_key=self._api_key,
            http_client=self._http_client,
        )

    def cancel(self) -> None:
        import httpx
        with self._lock:
            old_http = self._http_client
            self._http_client = httpx.Client()
            self._client = self._make_client()
        try:
            old_http.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        with self._lock:
            client = self._client
        # Anthropic 的 max_tokens 为必填，0 时 fallback 到 8192
        system_content = ""
        user_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
            else:
                user_messages.append(msg)

        kwargs: dict = dict(
            model=self._model,
            max_tokens=max_tokens if max_tokens > 0 else 8192,
            messages=user_messages,
        )
        if system_content:
            kwargs["system"] = system_content

        resp = client.messages.create(**kwargs)
        return resp.content[0].text if resp.content else ""


def create_llm_client(config: "LLMConfig") -> LLMClient:
    """工厂函数，按 provider 返回对应实现。"""
    if config.provider == "anthropic":
        return AnthropicClient(api_key=config.api_key, model=config.model)
    # 默认 openai_compatible
    return OpenAICompatibleClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )
