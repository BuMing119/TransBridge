# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Provider Prompt Cache 的内部标记、验证和适配协议。

业务层只描述稳定前缀和断点；本模块负责验证消息拓扑、判断缓存资格、剥离
内部元数据，并生成 OpenAI / Anthropic 的 Provider 私有请求形态。
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
import hashlib
import logging
import re
from typing import Literal, TypedDict

from transbridge.infra.llm_request_config import is_official_anthropic_endpoint

try:
    import tiktoken
except ImportError:  # The cache optimizer must not make translation unavailable.
    tiktoken = None

logger = logging.getLogger(__name__)

PROMPT_CACHE_METADATA_KEY = "_transbridge_prompt_cache"
_OFFICIAL_OPENAI_BASE = "https://api.openai.com/v1"

CacheProfile = Literal["translation_layered", "single_stable_prefix", "assistant_stable"]
CacheBreakpoint = Literal["A", "B", "FINAL"]

_OPENAI_EXPLICIT_MIN_TOKENS = 1024
# Anthropic 没有离线官方 tokenizer。使用 o200k 估算时额外保留 25% 安全余量。
_ANTHROPIC_ESTIMATE_SAFETY_NUMERATOR = 5
_ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR = 4
# Claude API 的已知最低长度。未列出的模型由服务端判断。
_ANTHROPIC_CACHE_MIN_TOKENS = {
    "claude-fable-5": 512,
    "claude-fable-5-1": 512,
    "claude-fable-5-2": 512,
    "claude-haiku-5-5": 512,
    "claude-mythos-5": 512,
    "claude-mythos-5-1": 512,
    "claude-mythos-5-2": 512,
    "claude-opus-5": 512,
    "claude-opus-5-5": 512,
    "claude-sonnet-5-5": 512,
    "claude-mythos-preview": 2048,
    "claude-opus-4-7": 2048,
    "claude-haiku-4-5": 4096,
    "claude-opus-4-5": 4096,
    "claude-opus-4-6": 4096,
    "claude-opus-4": 1024,
    "claude-opus-4-1": 1024,
    "claude-opus-4-8": 1024,
    "claude-sonnet-4": 1024,
    "claude-sonnet-4-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-sonnet-5": 1024,
}

# 显式断点能力采用集中式规则。未知模型继续走 automatic_prefix。
_OPENAI_EXPLICIT_MODEL = re.compile(r"^gpt-(?:5\.[6-9]|6(?:\.[0-9]+)?)(?:-|$)")
_REJECTED_PARAMETER = re.compile(
    r"\b(?:invalid|unknown|unrecognized|unsupported|unexpected|forbidden|extra_forbidden|rejected|"
    r"not (?:supported|permitted|allowed|recognized|valid|accepted))\b",
    re.IGNORECASE,
)


class PromptCacheDirective(TypedDict):
    """附挂在单条消息上的内部缓存指令。"""

    key: str
    profile: CacheProfile
    breakpoint: CacheBreakpoint


class OpenAIPromptCacheRequest(TypedDict):
    """OpenAI Chat Completions 的缓存适配结果。"""

    messages: list[dict]
    request_options: dict
    cache_mode: Literal["explicit_breakpoints", "automatic_prefix", "disabled"]


def log_prompt_cache_strategy(*, provider: str, model: str, profile: str, strategy: str, reason: str) -> None:
    logger.debug(
        "Prompt cache request strategy: provider=%s model=%s profile=%s strategy=%s reason=%s",
        provider,
        model,
        profile,
        strategy,
        reason,
    )


def is_cache_parameter_rejection(exc: Exception, *, fields: tuple[str, ...]) -> bool:
    """Require a client error that identifies a cache parameter."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if not fields or status not in (400, 422):
        return False
    field_pattern = r"\b(?:" + "|".join(re.escape(field) for field in fields) + r")\b"
    param = getattr(exc, "param", None)
    if isinstance(param, str) and param:
        return bool(re.search(field_pattern, param, re.IGNORECASE))
    message = getattr(exc, "message", None)
    message = message if isinstance(message, str) else str(exc)
    return any(
        re.search(field_pattern, detail, re.IGNORECASE) and _REJECTED_PARAMETER.search(detail)
        for detail in re.split(r"[;\n]", message)
    )


def _sha24(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def build_prompt_cache_key(namespace: str, stable_prefix: str) -> str:
    """根据稳定前缀文本与固定 namespace 计算 cache key。"""
    return f"{namespace}.{_sha24(stable_prefix)}"


def attach_prompt_cache_directive(
    message: dict,
    *,
    cache_key: str,
    profile: CacheProfile,
    breakpoint: CacheBreakpoint,
) -> dict:
    """为消息附加内部缓存指令，且不修改传入对象。"""
    return {
        **message,
        PROMPT_CACHE_METADATA_KEY: {
            "key": cache_key,
            "profile": profile,
            "breakpoint": breakpoint,
        },
    }


def _extract_one(message: dict) -> PromptCacheDirective | None:
    value = message.get(PROMPT_CACHE_METADATA_KEY)
    if not isinstance(value, dict):
        return None
    key = value.get("key")
    profile = value.get("profile")
    breakpoint = value.get("breakpoint")
    if not isinstance(key, str) or not key:
        return None
    if profile not in ("translation_layered", "single_stable_prefix", "assistant_stable"):
        return None
    if breakpoint not in ("A", "B", "FINAL"):
        return None
    return PromptCacheDirective(key=key, profile=profile, breakpoint=breakpoint)


def _strip_message(message: dict) -> dict:
    return {
        key: value
        for key, value in message.items()
        if key not in {PROMPT_CACHE_METADATA_KEY, "_transbridge_assistant_context"}
    }


def extract_prompt_cache_directives(
    messages: list[dict],
) -> tuple[list[dict], tuple[PromptCacheDirective, ...]]:
    """剥离内部元数据并按消息顺序返回所有可解析指令。"""
    clean = [_strip_message(message) for message in messages]
    directives = tuple(directive for message in messages if (directive := _extract_one(message)) is not None)
    return clean, directives


def validate_prompt_cache_directives(
    messages: list[dict],
    profile: CacheProfile,
) -> tuple[list[dict], tuple[PromptCacheDirective, ...]]:
    """验证缓存标记的完整消息拓扑；非法时清理标记并降级为无缓存。"""
    clean, _ = extract_prompt_cache_directives(messages)
    slots = tuple(_extract_one(message) for message in messages)
    has_metadata = tuple(PROMPT_CACHE_METADATA_KEY in message for message in messages)

    if not any(has_metadata):
        return clean, ()
    if any(present and directive is None for present, directive in zip(has_metadata, slots, strict=True)):
        _log_invalid_topology(profile)
        return clean, ()

    if profile == "translation_layered":
        valid = _validate_layered_topology(messages, slots, has_metadata)
    elif profile == "assistant_stable":
        from transbridge.infra.assistant_prompt_cache import validate_assistant_topology

        valid = validate_assistant_topology(messages, slots, has_metadata)
    else:
        valid = _validate_single_topology(messages, slots, has_metadata)
    if not valid:
        _log_invalid_topology(profile)
        return clean, ()

    directives = tuple(directive for directive in slots if directive is not None)
    return clean, directives


def _validate_layered_topology(
    messages: list[dict],
    slots: tuple[PromptCacheDirective | None, ...],
    has_metadata: tuple[bool, ...],
) -> bool:
    if len(messages) != 3 or [message.get("role") for message in messages] != ["system", "system", "user"]:
        return False
    first, second, third = slots
    if first is None or second is None or third is not None or has_metadata[2]:
        return False
    return (
        first["profile"] == second["profile"] == "translation_layered"
        and first["breakpoint"] == "A"
        and second["breakpoint"] == "B"
        and first["key"] == second["key"]
    )


def _validate_single_topology(
    messages: list[dict],
    slots: tuple[PromptCacheDirective | None, ...],
    has_metadata: tuple[bool, ...],
) -> bool:
    if len(messages) != 2 or [message.get("role") for message in messages] != ["system", "user"]:
        return False
    first, second = slots
    if first is None or second is not None or has_metadata[1]:
        return False
    return first["profile"] == "single_stable_prefix" and first["breakpoint"] == "FINAL"


def _log_invalid_topology(profile: CacheProfile | str) -> None:
    logger.warning("Prompt cache directives 不符合 %s 拓扑，请求将省略缓存参数", profile)


def _validated_directives(
    messages: list[dict],
) -> tuple[list[dict], tuple[PromptCacheDirective, ...]]:
    clean, parsed = extract_prompt_cache_directives(messages)
    if not any(PROMPT_CACHE_METADATA_KEY in message for message in messages):
        return clean, ()
    if not parsed:
        _log_invalid_topology("unknown")
        return clean, ()
    profiles = {directive["profile"] for directive in parsed}
    if len(profiles) != 1:
        _log_invalid_topology("mixed")
        return clean, ()
    return validate_prompt_cache_directives(messages, parsed[0]["profile"])


def is_official_openai_base_url(base_url: str) -> bool:
    """仅移除尾部斜杠后严格匹配的官方 URL 才允许接收私有字段。"""
    return bool(base_url) and base_url.rstrip("/") == _OFFICIAL_OPENAI_BASE


def openai_cache_capability(
    model: str,
) -> Literal["explicit_breakpoints", "automatic_prefix", "disabled"]:
    """集中判断 OpenAI 模型缓存能力；未知模型不猜测显式断点支持。"""
    normalized = (model or "").strip().lower()
    if not normalized:
        return "disabled"
    if _OPENAI_EXPLICIT_MODEL.match(normalized):
        return "explicit_breakpoints"
    return "automatic_prefix"


@lru_cache(maxsize=32)
def _encoding_for_model(model: str):
    if tiktoken is None:
        logger.warning("未安装 tiktoken，无法在本地验证显式缓存门槛")
        return None
    try:
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")
    except Exception:
        logger.warning("无法加载 prompt cache tokenizer，无法在本地验证显式缓存门槛", exc_info=True)
        return None


@lru_cache(maxsize=512)
def estimate_prompt_tokens(model: str, text: str) -> int | None:
    """使用 OpenAI tokenizer 统计文本 token；编码不可用时返回 None。"""
    encoding = _encoding_for_model(model)
    if encoding is None:
        return None
    try:
        return len(encoding.encode(text))
    except Exception:
        logger.warning("无法计算 prompt cache token 数，无法在本地验证显式缓存门槛", exc_info=True)
        return None


def _message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in ("text", "input_text")
        )
    return ""


def _stable_prefixes(clean: list[dict], profile: CacheProfile) -> tuple[str, ...]:
    if profile == "translation_layered":
        common = _message_text(clean[0])
        mode = _message_text(clean[1])
        return common, f"{common}\n{mode}"
    return (_message_text(clean[0]),)


def _anthropic_cache_min_tokens(model: str) -> int | None:
    normalized = model.strip().lower()
    family, _, suffix = normalized.rpartition("-")
    if suffix == "latest":
        normalized = family
    elif re.fullmatch(r"20[0-9]{6}", suffix):
        try:
            date.fromisoformat(suffix)
        except ValueError:
            return None
        normalized = family
    return _ANTHROPIC_CACHE_MIN_TOKENS.get(normalized)


def _prefixes_are_cacheable(prefixes: tuple[str, ...], threshold: int, *, tokenizer_model: str) -> bool:
    counts = (estimate_prompt_tokens(tokenizer_model, prefix) for prefix in prefixes)
    return all(count is not None and count >= threshold for count in counts)


def _anthropic_breakpoint_eligibility(
    model: str, base_url: str, profile: CacheProfile, clean: list[dict]
) -> tuple[bool, str]:
    if not is_official_anthropic_endpoint(base_url):
        return False, "endpoint_not_official"
    prefixes = _stable_prefixes(clean, profile)
    if not all(_message_text(message) for message in clean[: len(prefixes)]):
        return False, "empty_breakpoint"
    provider_threshold = _anthropic_cache_min_tokens(model)
    if provider_threshold is None:
        # The official API processes short prompts without caching or returning an error.
        return True, "server_checks_unknown_model_minimum"
    threshold = (
        provider_threshold * _ANTHROPIC_ESTIMATE_SAFETY_NUMERATOR + _ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR - 1
    ) // _ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR
    eligible = _prefixes_are_cacheable(prefixes, threshold, tokenizer_model="gpt-5.6")
    return eligible, "local_minimum_met" if eligible else "local_minimum_not_met_or_unavailable"


def prepare_openai_chat_cache_request(
    *,
    model: str,
    base_url: str,
    messages: list[dict],
    cache_key: str | None = None,
) -> OpenAIPromptCacheRequest:
    """验证并转换 OpenAI 普通/流式请求的客户端缓存参数。"""
    clean, directives = _validated_directives(messages)
    if not directives:
        return _disabled_openai_request(clean)

    profile = directives[0]["profile"]
    if not is_official_openai_base_url(base_url):
        log_prompt_cache_strategy(
            provider="openai", model=model, profile=profile, strategy="standard_request", reason="endpoint_not_official"
        )
        return _disabled_openai_request(clean)

    if profile == "assistant_stable":
        from transbridge.infra.assistant_prompt_cache import prepare_assistant_openai

        return prepare_assistant_openai(messages=clean, model=model, base_url=base_url, key=directives[0]["key"])

    capability = openai_cache_capability(model)
    if capability == "disabled":
        return _disabled_openai_request(clean)

    key = directives[0]["key"] or cache_key or ""
    if capability == "automatic_prefix":
        log_prompt_cache_strategy(
            provider="openai", model=model, profile=profile, strategy=capability, reason="server_checks_prefix_length"
        )
        return {
            "messages": clean,
            "request_options": {"prompt_cache_key": key},
            "cache_mode": "automatic_prefix",
        }
    eligible = _prefixes_are_cacheable(
        _stable_prefixes(clean, profile),
        _OPENAI_EXPLICIT_MIN_TOKENS,
        tokenizer_model=model,
    )
    log_prompt_cache_strategy(
        provider="openai",
        model=model,
        profile=profile,
        strategy=capability if eligible else "standard_request",
        reason="local_minimum_met" if eligible else "local_minimum_not_met_or_unavailable",
    )
    if not eligible:
        return _disabled_openai_request(clean)
    return _build_openai_explicit(clean, profile, key)


def build_openai_translation_cache_options(
    *,
    model: str,
    base_url: str,
    messages: list[dict],
    cache_key: str,
) -> OpenAIPromptCacheRequest:
    """兼容旧调用名，统一委托给共享 OpenAI 转换器。"""
    return prepare_openai_chat_cache_request(
        model=model,
        base_url=base_url,
        messages=messages,
        cache_key=cache_key,
    )


def _disabled_openai_request(clean: list[dict]) -> OpenAIPromptCacheRequest:
    return {"messages": clean, "request_options": {}, "cache_mode": "disabled"}


def _build_openai_explicit(
    clean: list[dict],
    profile: CacheProfile,
    cache_key: str,
) -> OpenAIPromptCacheRequest:
    breakpoint_indexes = {0, 1} if profile == "translation_layered" else {0}
    out_messages: list[dict] = []
    for index, message in enumerate(clean):
        if index in breakpoint_indexes:
            block = {
                "type": "text",
                "text": _message_text(message),
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
            out_messages.append({"role": "system", "content": [block]})
        else:
            out_messages.append(message)
    return {
        "messages": out_messages,
        "request_options": {
            "prompt_cache_options": {"mode": "explicit"},
            "prompt_cache_key": cache_key,
        },
        "cache_mode": "explicit_breakpoints",
    }


def build_anthropic_system_blocks(
    messages: list[dict],
    *,
    cache_key: str = "",
    model: str = "",
    base_url: str = "",
    enable_cache: bool = True,
) -> tuple[list[dict], list[dict]]:
    """转换 Anthropic system blocks；非法或不够长的前缀不附加 cache_control。"""
    del cache_key  # Anthropic 按完整前缀匹配，不接收应用侧 key。
    clean, directives = _validated_directives(messages)
    profile = directives[0]["profile"] if directives else None
    breakpoint_indexes = set()
    if enable_cache and profile and profile != "assistant_stable":
        cacheable, reason = _anthropic_breakpoint_eligibility(model, base_url, profile, clean)
        if cacheable:
            breakpoint_indexes = {0, 1} if profile == "translation_layered" else {0}
        log_prompt_cache_strategy(
            provider="anthropic",
            model=model,
            profile=profile,
            strategy="explicit_breakpoints" if cacheable else "standard_request",
            reason=reason,
        )

    system_blocks: list[dict] = []
    user_messages: list[dict] = []
    for index, message in enumerate(clean):
        if message.get("role") == "system":
            block: dict = {"type": "text", "text": _message_text(message)}
            if index in breakpoint_indexes:
                block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(block)
        else:
            user_messages.append(message)
    return system_blocks, user_messages
