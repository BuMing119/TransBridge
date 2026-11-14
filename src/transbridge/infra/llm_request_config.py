# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve provider addresses and output limits before dispatching an LLM request."""

import os

import httpx

ANTHROPIC_DEFAULT_ENDPOINT = "https://api.anthropic.com"
OPENAI_DEFAULT_ENDPOINT = "https://api.openai.com/v1"
ASSISTANT_OUTPUT_TOKENS = 4096
TRANSLATION_OUTPUT_TOKENS = 8192


def resolve_anthropic_endpoint(base_url: str | None = None) -> str:
    """Return the address for the Anthropic SDK, with its configured path intact."""
    value = (base_url or "").strip() or os.environ.get("ANTHROPIC_BASE_URL", "").strip() or ANTHROPIC_DEFAULT_ENDPOINT
    try:
        endpoint = httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Anthropic Base URL 必须是有效的 HTTP(S) 地址。") from None
    if endpoint.scheme not in {"http", "https"} or not endpoint.host:
        raise ValueError("Anthropic Base URL 必须是有效的 HTTP(S) 地址。")
    if endpoint.host.rstrip(".") == "api.openai.com":
        raise ValueError("Anthropic Base URL 指向 OpenAI。请在设置 → AI 服务中清空地址，或填写 Anthropic 兼容地址。")
    if "?" in value or "#" in value:
        raise ValueError("请移除 Anthropic Base URL 中的查询参数和片段，以便 SDK 正确拼接请求路径。")
    return value


def is_official_anthropic_endpoint(base_url: str) -> bool:
    """Match the official Messages endpoint without extending its capabilities to proxies."""
    try:
        endpoint = httpx.URL(base_url)
    except (ValueError, httpx.InvalidURL):
        return False
    return (
        endpoint.scheme == "https"
        and endpoint.netloc == b"api.anthropic.com"
        and endpoint.raw_path == b"/"
        and not endpoint.userinfo
        and not endpoint.fragment
    )


def resolve_output_tokens(
    provider: str,
    max_tokens: int,
    *,
    default: int = TRANSLATION_OUTPUT_TOKENS,
) -> int:
    """Treat zero as automatic and preserve valid explicit limits."""
    if type(max_tokens) is not int or max_tokens < 0:
        raise ValueError("max_tokens 必须是非负整数，0 表示自动。")
    if max_tokens == 0 and provider == "anthropic":
        if type(default) is not int or default <= 0:
            raise ValueError("Anthropic 默认输出 Token 上限必须是正整数。")
        return default
    return max_tokens
