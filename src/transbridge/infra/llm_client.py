"""
LLM 客户端抽象层。

提供：
- LLMClient (ABC)
- OpenAICompatibleClient
- AnthropicClient
- create_llm_client() 工厂函数
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import threading
import time
from typing import TYPE_CHECKING

import httpx

from transbridge.infra.llm_reasoning_protocols import (
    AnthropicReasoningProtocolMixin,
    OpenAIReasoningProtocolMixin,
)

if TYPE_CHECKING:
    from transbridge.paratranz.config_manager import LLMConfig

logger = logging.getLogger(__name__)

# LLM API 调用通常需要 30-60+ 秒，httpx 默认 5s 超时会导致误报超时失败
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# SDK 内置重试次数默认值（429/5xx/连接错误自动重试）
_DEFAULT_MAX_RETRIES = 2

# Provider cache 参数被 Provider 拒绝时，仅去掉缓存参数降级重试一次的信号。
_PROMPT_CACHE_REJECTION_KEYWORDS = ("prompt_cache", "prompt cache", "cache_control", "cache")


def _is_cache_rejection(exc: Exception) -> bool:
    """判断异常是否为 Provider 拒绝缓存参数（400/422 且信息含缓存关键字）。

    仅用于触发「去掉缓存参数重试一次」的降级；其他错误原样上抛。
    """
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) else str(exc)
    lowered = text.lower()
    return any(k in lowered for k in _PROMPT_CACHE_REJECTION_KEYWORDS)


def _is_system_blocks_unsupported(exc: Exception) -> bool:
    """判断 Anthropic 异常是否为「老版本 SDK 不支持 system 为 content blocks 列表」。

    老版本 SDK 会在本地（联网前）因 system 为 list 而抛非 HTTP 校验错误；
    网络/HTTP/状态类错误不算，交给上层原样上抛。
    """
    if isinstance(exc, httpx.HTTPError):
        return False
    if getattr(exc, "status_code", None) is not None:
        return False
    return "system" in str(exc).lower()


def _has_anthropic_cache_control(system_blocks: list[dict]) -> bool:
    return any(isinstance(block, dict) and "cache_control" in block for block in system_blocks)


def _anthropic_system_text(system_blocks: list[dict]) -> str:
    return "\n".join(
        block.get("text", "") for block in system_blocks if isinstance(block, dict) and block.get("type") == "text"
    )


def _require_anthropic_max_tokens(max_tokens: int) -> None:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError(
            "Anthropic API requires a positive max_tokens value; "
            "configure a value greater than 0 in the AI translator's '输出 Token' setting."
        )


class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        """发送消息并返回模型回复文本。供应商支持时，max_tokens=0 表示不限制。"""

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


class OpenAICompatibleClient(OpenAIReasoningProtocolMixin, LLMClient):
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
        return self._chat(messages, max_tokens, reasoning_patch=None)

    def _chat(self, messages: list[dict], max_tokens: int, *, reasoning_patch) -> str:
        from transbridge.infra.prompt_cache import (
            extract_prompt_cache_directives,
            prepare_openai_chat_cache_request,
        )

        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            # 普通与流式共用同一转换器，避免缓存参数漂移。
            req = prepare_openai_chat_cache_request(model=self._model, base_url=self._base_url, messages=messages)
            kwargs: dict = dict(model=self._model, messages=req["messages"])
            if reasoning_patch is not None:
                kwargs.update(reasoning_patch.standard)
            extra_body = dict(req["request_options"])
            if reasoning_patch is not None:
                extra_body.update(reasoning_patch.extra_body)
            if extra_body:
                # request_options 含 prompt_cache_options 等非标准字段，走 extra_body，
                # 不污染标准参数。
                kwargs["extra_body"] = extra_body
            if max_tokens > 0:
                kwargs["max_tokens"] = max_tokens
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as exc:
                # 缓存参数被 Provider 拒绝（401/400 含 cache 相关错误）时，
                # 仅去掉缓存参数（干净标准消息）重试一次；第二次失败原样上抛。
                if _is_cache_rejection(exc):
                    clean, _ = extract_prompt_cache_directives(messages)
                    retry_kwargs: dict = dict(model=self._model, messages=clean)
                    if reasoning_patch is not None:
                        retry_kwargs.update(reasoning_patch.standard)
                        if reasoning_patch.extra_body:
                            retry_kwargs["extra_body"] = dict(reasoning_patch.extra_body)
                    if max_tokens > 0:
                        retry_kwargs["max_tokens"] = max_tokens
                    logger.warning(
                        "OpenAI 缓存参数被拒绝(%s)，降级为无缓存重试: model=%s",
                        exc,
                        self._model,
                    )
                    resp = client.chat.completions.create(**retry_kwargs)
                else:
                    raise
            return resp.choices[0].message.content or ""
        except Exception:
            logger.exception("OpenAI chat() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        return self._chat_stream(messages, max_tokens, chunk_callback, reasoning_patch=None)

    def _chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback, *, reasoning_patch) -> str:
        from transbridge.infra.prompt_cache import (
            extract_prompt_cache_directives,
            prepare_openai_chat_cache_request,
        )

        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            # 与 chat() 共用同一转换器。
            req = prepare_openai_chat_cache_request(model=self._model, base_url=self._base_url, messages=messages)
            kwargs: dict = dict(model=self._model, messages=req["messages"], stream=True)
            if reasoning_patch is not None:
                kwargs.update(reasoning_patch.standard)
            extra_body = dict(req["request_options"])
            if reasoning_patch is not None:
                extra_body.update(reasoning_patch.extra_body)
            if extra_body:
                kwargs["extra_body"] = extra_body
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
            except Exception as exc:
                if _is_cache_rejection(exc):
                    clean, _ = extract_prompt_cache_directives(messages)
                    retry_kwargs: dict = dict(model=self._model, messages=clean, stream=True)
                    if reasoning_patch is not None:
                        retry_kwargs.update(reasoning_patch.standard)
                        if reasoning_patch.extra_body:
                            retry_kwargs["extra_body"] = dict(reasoning_patch.extra_body)
                    if max_tokens > 0:
                        retry_kwargs["max_tokens"] = max_tokens
                    logger.warning(
                        "OpenAI chat_stream 缓存参数被拒绝(%s)，降级为无缓存重试: model=%s",
                        exc,
                        self._model,
                    )
                    with client.chat.completions.create(**retry_kwargs) as stream:
                        for chunk in stream:
                            delta = chunk.choices[0].delta.content or ""
                            if delta:
                                full_text += delta
                                chunk_callback(delta)
                else:
                    raise
        except Exception:
            logger.exception("OpenAI chat_stream() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1
        return full_text


class AnthropicClient(AnthropicReasoningProtocolMixin, LLMClient):
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
        from transbridge.infra.prompt_cache import build_anthropic_system_blocks

        _require_anthropic_max_tokens(max_tokens)
        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            # 普通与流式共用同一转换器：把内部指令 system 消息转为
            # 带 ephemeral cache_control 的 content blocks + user_messages。
            system_blocks, user_messages = build_anthropic_system_blocks(
                messages,
                model=self._model,
            )
            kwargs: dict = dict(
                model=self._model,
                max_tokens=max_tokens,
                messages=user_messages,
            )
            if system_blocks:
                kwargs["system"] = system_blocks
            try:
                resp = client.messages.create(**kwargs)
            except Exception as exc:
                if _is_cache_rejection(exc) and _has_anthropic_cache_control(system_blocks):
                    no_cache_blocks, no_cache_messages = build_anthropic_system_blocks(
                        messages,
                        model=self._model,
                        enable_cache=False,
                    )
                    retry_kwargs = {
                        **kwargs,
                        "system": no_cache_blocks,
                        "messages": no_cache_messages,
                    }
                    logger.warning(
                        "Anthropic 缓存参数被拒绝(%s)，降级为无缓存重试: model=%s",
                        exc,
                        self._model,
                    )
                    resp = client.messages.create(**retry_kwargs)
                # 老版本 SDK 不支持 system 为 content blocks 列表时，降级为字符串重试一次。
                elif _is_system_blocks_unsupported(exc) and system_blocks:
                    kwargs["system"] = _anthropic_system_text(system_blocks)
                    logger.warning(
                        "Anthropic 不支持 system content blocks，降级为字符串 system 重试: model=%s",
                        self._model,
                    )
                    resp = client.messages.create(**kwargs)
                else:
                    raise
            return resp.content[0].text if resp.content else ""
        except Exception:
            logger.exception("Anthropic chat() 调用失败: model=%s, messages_count=%d", self._model, len(messages))
            raise
        finally:
            with self._lock:
                self._active_requests -= 1

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        from transbridge.infra.prompt_cache import build_anthropic_system_blocks

        _require_anthropic_max_tokens(max_tokens)
        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            # 与 chat() 共用同一转换器。
            system_blocks, user_messages = build_anthropic_system_blocks(
                messages,
                model=self._model,
            )
            kwargs: dict = dict(
                model=self._model,
                max_tokens=max_tokens,
                messages=user_messages,
            )
            if system_blocks:
                kwargs["system"] = system_blocks

            full_text = ""
            try:
                with client.messages.stream(**kwargs) as stream:
                    for text in stream.text_stream:
                        full_text += text
                        chunk_callback(text)
            except Exception as exc:
                if _is_cache_rejection(exc) and _has_anthropic_cache_control(system_blocks):
                    no_cache_blocks, no_cache_messages = build_anthropic_system_blocks(
                        messages,
                        model=self._model,
                        enable_cache=False,
                    )
                    retry_kwargs = {
                        **kwargs,
                        "system": no_cache_blocks,
                        "messages": no_cache_messages,
                    }
                    logger.warning(
                        "Anthropic chat_stream 缓存参数被拒绝(%s)，降级为无缓存重试: model=%s",
                        exc,
                        self._model,
                    )
                    with client.messages.stream(**retry_kwargs) as stream:
                        for text in stream.text_stream:
                            full_text += text
                            chunk_callback(text)
                elif _is_system_blocks_unsupported(exc) and system_blocks:
                    kwargs["system"] = _anthropic_system_text(system_blocks)
                    logger.warning(
                        "Anthropic chat_stream 不支持 system content blocks，降级为字符串 system 重试: model=%s",
                        self._model,
                    )
                    with client.messages.stream(**kwargs) as stream:
                        for text in stream.text_stream:
                            full_text += text
                            chunk_callback(text)
                else:
                    raise
        except Exception:
            logger.exception(
                "Anthropic chat_stream() 调用失败: model=%s, messages_count=%d",
                self._model,
                len(messages),
            )
            raise
        finally:
            with self._lock:
                self._active_requests -= 1
        return full_text


def create_llm_client(config: LLMConfig) -> LLMClient:
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
