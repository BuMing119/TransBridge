"""Resolve assistant capacity without inferring capabilities of proxy endpoints."""

from dataclasses import dataclass
from urllib.parse import urlsplit

FALLBACK_CONTEXT_WINDOW = 131072
# Verified 2026-09-13: https://api-docs.deepseek.com/quick_start/pricing/
# Use decimal 1M conservatively; do not match arbitrary model name prefixes.
_DEEPSEEK_WINDOWS = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
}


@dataclass(frozen=True)
class ContextCapacity:
    window: int
    source: str


def resolve_context_capacity(config, *, override=None) -> ContextCapacity:
    configured = getattr(config, "assistant_context_window", 0) if override is None else override
    if type(configured) is not int or configured < 0:
        raise ValueError("助手上下文窗口必须是非负整数；0 表示自动。")
    if configured:
        return ContextCapacity(configured, "手动配置")
    endpoint = urlsplit(str(getattr(config, "base_url", "")))
    model = str(getattr(config, "model", "")).strip()
    if (
        getattr(config, "provider", "") == "openai_compatible"
        and endpoint.scheme == "https"
        and endpoint.netloc.lower() == "api.deepseek.com"
        and endpoint.path.rstrip("/") in {"", "/v1"}
        and not endpoint.query
        and not endpoint.fragment
        and model in _DEEPSEEK_WINDOWS
    ):
        return ContextCapacity(_DEEPSEEK_WINDOWS[model], "DeepSeek 官方模型规格（2026-09-13）")
    return ContextCapacity(FALLBACK_CONTEXT_WINDOW, "未识别服务容量，使用默认 128K；可按服务商规格手动调整")
