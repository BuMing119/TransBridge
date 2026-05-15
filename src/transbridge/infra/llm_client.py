"""
LLM 客户端抽象层。

提供：
- LLMClient (ABC)
- OpenAICompatibleClient
- AnthropicClient
- create_llm_client() 工厂函数
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.transbridge.paratranz.config_manager import LLMConfig

logger = logging.getLogger(__name__)

# LLM API 调用通常需要 30-60+ 秒，httpx 默认 5s 超时会导致误报超时失败
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# SDK 内置重试次数默认值（429/5xx/连接错误自动重试）
_DEFAULT_MAX_RETRIES = 2


class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        """发送消息并返回模型回复文本。max_tokens=0 表示不限制（由模型默认）。"""

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        """流式调用，每收到一个 chunk 即调用 chunk_callback(text)，最终返回完整文本。
        默认实现：退化为普通 chat，一次性回调完整响应。子类可覆盖以实现真正的流式。
        """
        result = self.chat(messages, max_tokens)
        if result:
            chunk_callback(result)
        return result

    def cancel(self) -> None:
        """中断当前进行中的请求（关闭 HTTP 连接），并重建客户端供后续使用。

        DESIGN LIMITATION (QA-007):
        - 调用 cancel() 时，chat()/chat_stream() 可能正在锁外执行 API 调用。
          关闭旧 HTTP 客户端会导致进行中的请求收到连接错误，
          调用方应捕获异常并妥善处理（Worker 线程在 on_error 回调中已处理）。
        - 此处的锁仅保证 _client/_http_client 引用的原子替换，
          但不阻止并发请求继续使用其已持有的旧客户端引用。
        """


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str, max_retries: int = _DEFAULT_MAX_RETRIES):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._max_retries = max_retries
        self._lock = threading.Lock()
        self._http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
        self._client = self._make_client()
        self._active_requests = 0

    def __repr__(self) -> str:
        masked = ("***" + self._api_key[-4:]) if len(self._api_key) > 4 else "***"
        return f"{type(self).__name__}(api_key={masked!r}, base_url={self._base_url!r}, model={self._model!r})"

    def _make_client(self):
        from openai import OpenAI
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=self._http_client,
            max_retries=self._max_retries,
        )

    def cancel(self) -> None:
        with self._lock:
            old_http = self._http_client
            self._http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
            self._client = self._make_client()
        # QA-007 修复：等待进行中的请求完成，缩小 cancel() 关闭连接时
        # 打断 API 调用的竞态窗口。
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self._lock:
                if self._active_requests == 0:
                    break
            time.sleep(0.05)
        try:
            old_http.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        with self._lock:
            client = self._client
            self._active_requests += 1
        kwargs: dict = dict(model=self._model, messages=messages)
        if max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception:
            logger.exception("OpenAI chat() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        with self._lock:
            client = self._client
            self._active_requests += 1
        kwargs: dict = dict(model=self._model, messages=messages, stream=True)
        if max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        full_text = ""
        try:
            with client.chat.completions.create(**kwargs) as stream:
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_text += delta
                        chunk_callback(delta)
        except Exception:
            logger.exception("OpenAI chat_stream() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1
        return full_text


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str, max_retries: int = _DEFAULT_MAX_RETRIES):
        self._api_key = api_key
        self._model = model
        self._max_retries = max_retries
        self._lock = threading.Lock()
        self._http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
        self._client = self._make_client()
        self._active_requests = 0

    def __repr__(self) -> str:
        masked = ("***" + self._api_key[-4:]) if len(self._api_key) > 4 else "***"
        return f"{type(self).__name__}(api_key={masked!r}, model={self._model!r})"

    def _make_client(self):
        import anthropic
        return anthropic.Anthropic(
            api_key=self._api_key,
            http_client=self._http_client,
            max_retries=self._max_retries,
        )

    def cancel(self) -> None:
        with self._lock:
            old_http = self._http_client
            self._http_client = httpx.Client(timeout=_DEFAULT_TIMEOUT)
            self._client = self._make_client()
        # QA-007 修复：等待进行中的请求完成，缩小 cancel() 关闭连接时
        # 打断 API 调用的竞态窗口。
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self._lock:
                if self._active_requests == 0:
                    break
            time.sleep(0.05)
        try:
            old_http.close()
        except Exception:
            pass

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        with self._lock:
            client = self._client
            self._active_requests += 1
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

        try:
            resp = client.messages.create(**kwargs)
            return resp.content[0].text if resp.content else ""
        except Exception:
            logger.exception("Anthropic chat() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        with self._lock:
            client = self._client
            self._active_requests += 1
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

        full_text = ""
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    full_text += text
                    chunk_callback(text)
        except Exception:
            logger.exception("Anthropic chat_stream() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1
        return full_text


def create_llm_client(config: "LLMConfig") -> LLMClient:
    """工厂函数，按 provider 返回对应实现。"""
    max_retries = getattr(config, "llm_max_retries", _DEFAULT_MAX_RETRIES)
    if config.provider == "anthropic":
        return AnthropicClient(api_key=config.api_key, model=config.model, max_retries=max_retries)
    # 默认 openai_compatible
    return OpenAICompatibleClient(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        max_retries=max_retries,
    )
