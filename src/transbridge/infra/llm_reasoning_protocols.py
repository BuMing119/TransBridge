"""Protocol-specific probing and request mapping for LLM reasoning controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from transbridge.infra.llm_reasoning import (
    ReasoningCapability,
    ReasoningIntent,
    ReasoningRequestPatch,
)

_PROBE_MESSAGES = [{"role": "user", "content": "Reply with OK."}]
_VALIDATION_WORDS = ("invalid", "must be", "one of", "allowed", "expected", "literal", "enum")
_UNKNOWN_FIELD_WORDS = ("unknown parameter", "unrecognized", "unsupported parameter", "extra inputs")


@dataclass(frozen=True, slots=True)
class _OpenAIControlCandidate:
    mechanism: str
    field_names: tuple[str, ...]
    invalid_standard: dict[str, object]
    invalid_extra: dict[str, object]
    direct_standard: dict[str, object]
    direct_extra: dict[str, object]
    low_standard: dict[str, object]
    low_extra: dict[str, object]


_OPENAI_CANDIDATES = (
    _OpenAIControlCandidate(
        "reasoning_effort",
        ("reasoning_effort", "reasoning effort"),
        {"reasoning_effort": "__transbridge_probe__"},
        {},
        {"reasoning_effort": "none"},
        {},
        {"reasoning_effort": "low"},
        {},
    ),
    _OpenAIControlCandidate(
        "thinking_disabled",
        ("thinking",),
        {},
        {"thinking": {"type": "__transbridge_probe__"}},
        {},
        {"thinking": {"type": "disabled"}},
        {},
        {},
    ),
    _OpenAIControlCandidate(
        "enable_thinking",
        ("enable_thinking", "enable thinking"),
        {},
        {"enable_thinking": {"invalid": True}},
        {},
        {"enable_thinking": False},
        {},
        {},
    ),
)


class OpenAIReasoningProtocolMixin:
    """Reasoning extension implemented by an OpenAI-compatible client."""

    def detect_reasoning_capability(self):
        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            return detect_openai_reasoning_capability(client.chat.completions.create, model=self._model)
        finally:
            with self._lock:
                self._active_requests -= 1

    @staticmethod
    def build_reasoning_patch(capability, intent):
        return openai_reasoning_patch(capability, intent)

    @staticmethod
    def is_reasoning_control_rejection(exc: Exception, reasoning_patch) -> bool:
        return is_openai_reasoning_rejection(exc, reasoning_patch.mechanism)

    def chat_with_reasoning(self, messages: list[dict], max_tokens: int, reasoning_patch) -> str:
        return self._chat(messages, max_tokens, reasoning_patch=reasoning_patch)

    def chat_stream_with_reasoning(self, messages: list[dict], max_tokens: int, chunk_callback, reasoning_patch) -> str:
        return self._chat_stream(messages, max_tokens, chunk_callback, reasoning_patch=reasoning_patch)


class AnthropicReasoningProtocolMixin:
    """Reasoning extension implemented by an Anthropic Messages client."""

    @staticmethod
    def detect_reasoning_capability():
        return anthropic_reasoning_capability()

    @staticmethod
    def build_reasoning_patch(capability, intent):
        return anthropic_reasoning_patch(capability, intent)

    @staticmethod
    def is_reasoning_control_rejection(_exc: Exception, _reasoning_patch) -> bool:
        return False

    def chat_with_reasoning(self, messages: list[dict], max_tokens: int, _reasoning_patch) -> str:
        return self.chat(messages, max_tokens)

    def chat_stream_with_reasoning(
        self,
        messages: list[dict],
        max_tokens: int,
        chunk_callback,
        _reasoning_patch,
    ) -> str:
        return self.chat_stream(messages, max_tokens, chunk_callback)


def detect_openai_reasoning_capability(
    create: Callable[..., object],
    *,
    model: str,
) -> ReasoningCapability:
    """Use invalid-value handshakes so silently ignored fields never become supported."""

    recognized_but_unavailable = False
    direct_mechanism = ""
    low_mechanism = ""
    for candidate in _OPENAI_CANDIDATES:
        try:
            create(
                model=model,
                messages=_PROBE_MESSAGES,
                max_tokens=8,
                **candidate.invalid_standard,
                **({"extra_body": candidate.invalid_extra} if candidate.invalid_extra else {}),
            )
        except Exception as exc:
            classification = _classify_probe_error(exc, candidate.field_names)
            if classification == "unrelated":
                return ReasoningCapability.indeterminate()
            if classification == "unknown":
                continue
            if classification != "validated":
                continue
        else:
            # A successful request with a deliberately invalid value means a
            # permissive gateway may have dropped the field.
            continue

        recognized_but_unavailable = True
        try:
            response = create(
                model=model,
                messages=_PROBE_MESSAGES,
                max_tokens=8,
                **candidate.direct_standard,
                **({"extra_body": candidate.direct_extra} if candidate.direct_extra else {}),
            )
        except Exception as exc:
            classification = _classify_probe_error(exc, candidate.field_names)
            if classification == "unrelated":
                return ReasoningCapability.indeterminate()
        else:
            if not _has_reasoning_evidence(response):
                direct_mechanism = candidate.mechanism

        if candidate.low_standard or candidate.low_extra:
            try:
                create(
                    model=model,
                    messages=_PROBE_MESSAGES,
                    max_tokens=8,
                    **candidate.low_standard,
                    **({"extra_body": candidate.low_extra} if candidate.low_extra else {}),
                )
            except Exception as exc:
                classification = _classify_probe_error(exc, candidate.field_names)
                if classification == "unrelated":
                    return ReasoningCapability.indeterminate()
            else:
                low_mechanism = candidate.mechanism

        if direct_mechanism:
            return ReasoningCapability.supported_controls(direct=direct_mechanism, low=low_mechanism)

    if direct_mechanism or low_mechanism:
        return ReasoningCapability.supported_controls(direct=direct_mechanism, low=low_mechanism)
    if recognized_but_unavailable:
        return ReasoningCapability.unsupported()
    return ReasoningCapability.indeterminate()


def openai_reasoning_patch(
    capability: ReasoningCapability,
    intent: ReasoningIntent,
) -> ReasoningRequestPatch | None:
    mechanism = capability.direct_mechanism
    if intent is ReasoningIntent.PREFER_LOW:
        mechanism = capability.low_mechanism or capability.direct_mechanism
    candidate = next((value for value in _OPENAI_CANDIDATES if value.mechanism == mechanism), None)
    if candidate is None:
        return None
    if intent is ReasoningIntent.PREFER_LOW:
        if capability.low_mechanism:
            return ReasoningRequestPatch(mechanism, dict(candidate.low_standard), dict(candidate.low_extra))
        return ReasoningRequestPatch(mechanism, dict(candidate.direct_standard), dict(candidate.direct_extra))
    if intent is ReasoningIntent.PREFER_DIRECT:
        return ReasoningRequestPatch(mechanism, dict(candidate.direct_standard), dict(candidate.direct_extra))
    return None


def anthropic_reasoning_capability() -> ReasoningCapability:
    # In the Anthropic Messages protocol extended thinking is opt-in; omission
    # is therefore the stable direct-answer representation.
    return ReasoningCapability.supported("anthropic_omit_thinking")


def anthropic_reasoning_patch(
    capability: ReasoningCapability,
    intent: ReasoningIntent,
) -> ReasoningRequestPatch | None:
    if capability.direct_mechanism != "anthropic_omit_thinking" or intent is ReasoningIntent.INHERIT:
        return None
    return ReasoningRequestPatch(capability.direct_mechanism, {}, {})


def is_openai_reasoning_rejection(exc: Exception, mechanism: str) -> bool:
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return False
    candidate = next((value for value in _OPENAI_CANDIDATES if value.mechanism == mechanism), None)
    if candidate is None:
        return False
    text = _exception_text(exc)
    return any(field in text for field in candidate.field_names)


def _classify_probe_error(exc: Exception, fields: tuple[str, ...]) -> str:
    status = getattr(exc, "status_code", None)
    if status not in (400, 422):
        return "unrelated"
    text = _exception_text(exc)
    if not any(field in text for field in fields):
        return "unrelated"
    if any(word in text for word in _UNKNOWN_FIELD_WORDS):
        return "unknown"
    if any(word in text for word in _VALIDATION_WORDS) or "__transbridge_probe__" in text:
        return "validated"
    return "ambiguous"


def _exception_text(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return (message if isinstance(message, str) else str(exc)).lower()


def _has_reasoning_evidence(response: object) -> bool:
    choices = _value(response, "choices") or ()
    first = choices[0] if choices else None
    message = _value(first, "message")
    if _value(message, "reasoning_content") or _value(message, "reasoning"):
        return True
    usage = _value(response, "usage")
    details = _value(usage, "completion_tokens_details")
    try:
        return int(_value(details, "reasoning_tokens") or 0) > 0
    except (TypeError, ValueError):
        return False


def _value(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


__all__ = [
    "AnthropicReasoningProtocolMixin",
    "OpenAIReasoningProtocolMixin",
    "anthropic_reasoning_capability",
    "anthropic_reasoning_patch",
    "detect_openai_reasoning_capability",
    "is_openai_reasoning_rejection",
    "openai_reasoning_patch",
]
