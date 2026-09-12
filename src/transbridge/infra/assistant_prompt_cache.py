"""Assistant-only cache policy; stable history is distinct from translation topology."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from transbridge.infra.prompt_cache import (
    PROMPT_CACHE_METADATA_KEY,
    attach_prompt_cache_directive,
    estimate_prompt_tokens,
    is_official_openai_base_url,
    openai_cache_capability,
)

ASSISTANT_CONTEXT_KEY = "_transbridge_assistant_context"
ASSISTANT_CACHE_PROFILE = "assistant_stable"

# This profile deliberately does not inherit the generic unknown-model fallback.
_OPENAI_MODELS = ("gpt-4o", "gpt-4.1", "gpt-5", "gpt-6", "o1", "o3", "o4")
_ANTHROPIC_MODELS = (
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4-5",
    "claude-haiku-3-5",
    "claude-fable-5",
    "claude-mythos-5",
    "claude-mythos-preview",
)


def _clean_blocks(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [
        {
            key: _clean_blocks(item)
            for key, item in block.items()
            if key not in {"cache_control", "prompt_cache_breakpoint"}
        }
        if isinstance(block, dict)
        else block
        for block in value
    ]


def decorate_assistant_messages(messages: list[dict], *, namespace: str, enabled: bool = True) -> list[dict]:
    """Attach a stable request/config identity, never a digest of growing history.

    Layout identity survives disabling cache so protocol grouping cannot change
    when a user disables the optional optimization. This metadata is wire-only.
    """
    clean = []
    for message in messages:
        item = {
            key: value
            for key, value in message.items()
            if key not in {PROMPT_CACHE_METADATA_KEY, ASSISTANT_CONTEXT_KEY}
        }
        for key in ("content", "provider_content"):
            if key in item:
                item[key] = _clean_blocks(item[key])
        clean.append(item)
    if not clean:
        return clean
    key = "assistant." + hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:40]
    clean[0][ASSISTANT_CONTEXT_KEY] = {"cache_enabled": enabled}
    if enabled and clean[0].get("role") == "system":
        clean[0] = attach_prompt_cache_directive(
            clean[0],
            cache_key=key,
            profile=ASSISTANT_CACHE_PROFILE,
            breakpoint="FINAL",
        )
    return clean


def is_assistant_context(messages: list[dict]) -> bool:
    return bool(messages and isinstance(messages[0].get(ASSISTANT_CONTEXT_KEY), dict))


def validate_assistant_topology(messages, slots, has_metadata) -> bool:
    if not messages or messages[0].get("role") != "system" or slots[0] is None:
        return False
    first = slots[0]
    if first["profile"] != ASSISTANT_CACHE_PROFILE or first["breakpoint"] != "FINAL":
        return False
    if any(has_metadata[1:]):
        return False
    saw_history = False
    for message in messages:
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"} or (saw_history and role == "system"):
            return False
        saw_history |= role != "system"
    return True


def prepare_assistant_openai(*, messages: list[dict], model: str, base_url: str, key: str) -> dict:
    """Keep history intact; an optional fixed rule breakpoint never follows the tail."""
    result = {"messages": messages, "request_options": {}, "cache_mode": "disabled"}
    if not is_official_openai_base_url(base_url) or not model.lower().startswith(_OPENAI_MODELS):
        return result
    result["request_options"] = {"prompt_cache_key": key}
    result["cache_mode"] = "automatic_prefix"
    first_content = messages[0].get("content", "")
    if openai_cache_capability(model) != "explicit_breakpoints" or not isinstance(first_content, str):
        return result
    tokens = estimate_prompt_tokens(model, first_content)
    if tokens is None or tokens < 1024:
        return result
    converted = deepcopy(messages)
    converted[0]["content"] = [
        {
            "type": "text",
            "text": first_content,
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }
    ]
    result["messages"] = converted
    result["request_options"]["prompt_cache_options"] = {"mode": "explicit"}
    result["cache_mode"] = "explicit_breakpoints"
    return result


def anthropic_assistant_cache_options(messages: list[dict], *, model: str, base_url: str) -> dict:
    """Official automatic history caching preserves every existing content block."""
    from transbridge.infra.prompt_cache import validate_prompt_cache_directives

    if not is_assistant_context(messages):
        return {}
    if not messages[0][ASSISTANT_CONTEXT_KEY].get("cache_enabled"):
        return {}
    if base_url.rstrip("/") != "https://api.anthropic.com" or not model.lower().startswith(_ANTHROPIC_MODELS):
        return {}
    _, directives = validate_prompt_cache_directives(messages, ASSISTANT_CACHE_PROFILE)
    return {"cache_control": {"type": "ephemeral"}} if directives else {}
