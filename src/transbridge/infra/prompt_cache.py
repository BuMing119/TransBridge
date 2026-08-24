"""Provider Prompt Cache 的内部标记、验证和适配协议。

业务层只描述稳定前缀和断点；本模块负责验证消息拓扑、判断缓存资格、剥离
内部元数据，并生成 OpenAI / Anthropic 的 Provider 私有请求形态。
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import logging
from typing import Literal, TypedDict

try:
    import tiktoken
except ImportError:  # The cache optimizer must not make translation unavailable.
    tiktoken = None

logger = logging.getLogger(__name__)

PROMPT_CACHE_METADATA_KEY = "_transbridge_prompt_cache"
_OFFICIAL_OPENAI_BASE = "https://api.openai.com/v1"

CacheProfile = Literal["translation_layered", "single_stable_prefix"]
CacheBreakpoint = Literal["A", "B", "FINAL"]

_OPENAI_EXPLICIT_MIN_TOKENS = 1024
# 旧模型的官方门槛因模型而异（1024～2048）；未知旧模型按上限保守处理。
_OPENAI_AUTOMATIC_MIN_TOKENS = 2048
# Anthropic 没有离线官方 tokenizer。使用 o200k 估算时额外保留 25% 安全余量。
_ANTHROPIC_ESTIMATE_SAFETY_NUMERATOR = 5
_ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR = 4

# 显式断点能力采用集中式保守表。未知模型继续走 automatic_prefix。
_EXPLICIT_BREAKPOINT_PREFIXES = (
    "gpt-5.6",
    "gpt-5.7",
    "gpt-5.8",
    "gpt-5.9",
    "gpt-6",
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
    if profile not in ("translation_layered", "single_stable_prefix"):
        return None
    if breakpoint not in ("A", "B", "FINAL"):
        return None
    return PromptCacheDirective(key=key, profile=profile, breakpoint=breakpoint)


def _strip_message(message: dict) -> dict:
    return {key: value for key, value in message.items() if key != PROMPT_CACHE_METADATA_KEY}


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
    logger.warning("Prompt cache directives 不符合 %s 拓扑，降级为无缓存请求", profile)


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
    if any(normalized.startswith(prefix) for prefix in _EXPLICIT_BREAKPOINT_PREFIXES):
        return "explicit_breakpoints"
    return "automatic_prefix"


@lru_cache(maxsize=32)
def _encoding_for_model(model: str):
    if tiktoken is None:
        logger.warning("未安装 tiktoken，当前进程将禁用 prompt cache")
        return None
    try:
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")
    except Exception:
        logger.warning("无法加载 prompt cache tokenizer，当前进程将禁用显式缓存", exc_info=True)
        return None


@lru_cache(maxsize=512)
def estimate_prompt_tokens(model: str, text: str) -> int | None:
    """使用 OpenAI tokenizer 统计文本 token；编码不可用时返回 None 并安全禁用缓存。"""
    encoding = _encoding_for_model(model)
    if encoding is None:
        return None
    try:
        return len(encoding.encode(text))
    except Exception:
        logger.warning("无法计算 prompt cache token 数，当前请求将禁用缓存", exc_info=True)
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


def _anthropic_cache_min_tokens(model: str) -> int:
    normalized = (model or "").lower()
    if "fable-5" in normalized or "mythos-5" in normalized:
        return 512
    if "mythos-preview" in normalized or "opus-4-7" in normalized or "haiku-3-5" in normalized:
        return 2048
    if "opus-4-6" in normalized or "opus-4-5" in normalized or "haiku-4-5" in normalized:
        return 4096
    return 1024


def _prefixes_are_cacheable(
    *,
    provider: Literal["openai", "anthropic"],
    model: str,
    capability: Literal["explicit_breakpoints", "automatic_prefix"] | None,
    profile: CacheProfile,
    clean: list[dict],
) -> bool:
    prefixes = _stable_prefixes(clean, profile)
    if provider == "openai":
        threshold = (
            _OPENAI_EXPLICIT_MIN_TOKENS if capability == "explicit_breakpoints" else _OPENAI_AUTOMATIC_MIN_TOKENS
        )
        required_prefixes = prefixes if capability == "explicit_breakpoints" else prefixes[-1:]
        tokenizer_model = model
    else:
        provider_threshold = _anthropic_cache_min_tokens(model)
        threshold = (
            provider_threshold * _ANTHROPIC_ESTIMATE_SAFETY_NUMERATOR + _ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR - 1
        ) // _ANTHROPIC_ESTIMATE_SAFETY_DENOMINATOR
        required_prefixes = prefixes
        tokenizer_model = "gpt-5.6"

    counts = tuple(estimate_prompt_tokens(tokenizer_model, prefix) for prefix in required_prefixes)
    eligible = all(count is not None and count >= threshold for count in counts)
    if not eligible:
        logger.debug(
            "Prompt cache prefix below threshold: provider=%s model=%s profile=%s tokens=%s threshold=%d",
            provider,
            model,
            profile,
            counts,
            threshold,
        )
    return eligible


def prepare_openai_chat_cache_request(
    *,
    model: str,
    base_url: str,
    messages: list[dict],
    cache_key: str | None = None,
) -> OpenAIPromptCacheRequest:
    """验证并转换 OpenAI 普通/流式请求；任何不确定性都降级为标准消息。"""
    clean, directives = _validated_directives(messages)
    if not is_official_openai_base_url(base_url) or not directives:
        return _disabled_openai_request(clean)

    capability = openai_cache_capability(model)
    if capability == "disabled":
        return _disabled_openai_request(clean)

    profile = directives[0]["profile"]
    if not _prefixes_are_cacheable(
        provider="openai",
        model=model,
        capability=capability,
        profile=profile,
        clean=clean,
    ):
        return _disabled_openai_request(clean)

    key = directives[0]["key"] or cache_key or ""
    if capability == "automatic_prefix":
        return {
            "messages": clean,
            "request_options": {"prompt_cache_key": key},
            "cache_mode": "automatic_prefix",
        }
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
    enable_cache: bool = True,
) -> tuple[list[dict], list[dict]]:
    """转换 Anthropic system blocks；非法或不够长的前缀不附加 cache_control。"""
    del cache_key  # Anthropic 按完整前缀匹配，不接收应用侧 key。
    clean, directives = _validated_directives(messages)
    profile = directives[0]["profile"] if directives else None
    cacheable = bool(
        enable_cache
        and profile
        and _prefixes_are_cacheable(
            provider="anthropic",
            model=model,
            capability=None,
            profile=profile,
            clean=clean,
        )
    )
    breakpoint_indexes = ({0, 1} if profile == "translation_layered" else {0}) if cacheable else set()

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
