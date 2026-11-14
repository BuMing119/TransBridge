# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Protocol-specific probing and request mapping for LLM reasoning controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import re

from transbridge.infra.llm_reasoning import (
    ReasoningCapability,
    ReasoningIntent,
    ReasoningRequestPatch,
)
from transbridge.infra.llm_request_config import is_official_anthropic_endpoint

logger = logging.getLogger(__name__)

_PROBE_MESSAGES = [{"role": "user", "content": "Reply with OK."}]
_VALIDATION_WORDS = ("invalid", "must be", "one of", "allowed", "expected", "literal", "enum")
_UNKNOWN_FIELD_WORDS = ("unknown parameter", "unrecognized", "unsupported parameter", "extra inputs")
_ANTHROPIC_PROBE_VALUE = "__transbridge_probe__"
_ANTHROPIC_THINKING_MODES = {
    "anthropic_thinking_disabled": "disabled",
    "anthropic_thinking_between_tools": "between_tools",
}
_ANTHROPIC_LOW = "anthropic_effort_low"
_ANTHROPIC_VALUE_VALIDATION = (*_VALIDATION_WORDS, "should be", _ANTHROPIC_PROBE_VALUE)
_ANTHROPIC_UNKNOWN_FIELDS = (*_UNKNOWN_FIELD_WORDS, "unknown field", "unexpected field")
_ANTHROPIC_REJECTION_WORDS = (
    *_ANTHROPIC_VALUE_VALIDATION,
    *_ANTHROPIC_UNKNOWN_FIELDS,
    "not supported",
    "unsupported",
    "does not support",
    "doesn't support",
    "doesn’t support",
    "only supports",
    "instead of",
    "not permitted",
    "not accepted",
    "rejected",
    "cannot",
    "can't",
)
_ANTHROPIC_THINKING_ERROR = re.compile(
    r"(?<![\w-])thinking(?:\.|\s+)type\b"
    r"|(?<![\w-])[\"'`]?thinking[\"'`]?\s*(?::|\b(?:is|must|should|not)\b)"
    r"|\b(?:invalid|unknown parameter|unknown field|unrecognized parameter|unsupported parameter|"
    r"does not support|doesn['’]t support|only supports)[ :]+(?:extended |adaptive )?"
    r"[\"'`]?thinking(?![\w-])"
)
_ANTHROPIC_EFFORT_ERROR = re.compile(
    r"(?<![\w-])output_config(?:\.|\s*->\s*)effort(?![\w-])"
    r"|^\s*(?:invalid |unsupported )?[\"'`]?effort(?: level)?[\"'`]?\s*(?::|[\"'`]|\b(?:is|must|should|not)\b)"
)
_ANTHROPIC_SCHEMA_ERROR = re.compile(r"\boutput_config(?:\.|\s*->\s*)format\b|\bjson_schema\b")
_ANTHROPIC_MODEL_ERROR = re.compile(r"^\s*(?:invalid|unknown|unsupported|unrecognized) model\b")


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

    def detect_reasoning_capability(self):
        with self._lock:
            client = self._client
            self._active_requests += 1
        try:
            retrieve_model = None
            if is_official_anthropic_endpoint(str(client.base_url)):
                retrieve_model = getattr(getattr(client, "models", None), "retrieve", None)
            return detect_anthropic_reasoning_capability(
                client.messages.create,
                model=self._model,
                retrieve_model=retrieve_model,
            )
        finally:
            with self._lock:
                self._active_requests -= 1

    @staticmethod
    def build_reasoning_patch(capability, intent):
        return anthropic_reasoning_patch(capability, intent)

    @staticmethod
    def is_reasoning_control_rejection(exc: Exception, reasoning_patch) -> bool:
        return is_anthropic_reasoning_rejection(exc, reasoning_patch.mechanism)

    def chat_with_reasoning(self, messages: list[dict], max_tokens: int, reasoning_patch) -> str:
        return self._chat(messages, max_tokens, reasoning_patch=reasoning_patch)

    def chat_stream_with_reasoning(
        self,
        messages: list[dict],
        max_tokens: int,
        chunk_callback,
        reasoning_patch,
    ) -> str:
        return self._chat_stream(messages, max_tokens, chunk_callback, reasoning_patch=reasoning_patch)


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


def detect_anthropic_reasoning_capability(
    create: Callable[..., object],
    *,
    model: str,
    retrieve_model: Callable[[str], object] | None = None,
) -> ReasoningCapability:
    """Use official metadata or validate each control on the Messages endpoint."""
    capabilities = None
    if retrieve_model is not None:
        try:
            # The locked SDK's ModelInfo retains capability fields as extras.
            capabilities = _value(retrieve_model(model), "capabilities")
        except Exception as exc:
            if getattr(exc, "status_code", None) not in (404, 405, 501):
                _log_anthropic_probe_failure(exc, endpoint="Models")
                return ReasoningCapability.indeterminate()

    thinking_types = _value(_value(capabilities, "thinking"), "types")
    effort = _value(capabilities, "effort")
    direct_support = {
        mechanism: _anthropic_support(thinking_types, mode) for mechanism, mode in _ANTHROPIC_THINKING_MODES.items()
    }
    low = _anthropic_support(effort, "low")
    if low is None and _value(effort, "supported") is False:
        low = False
    try:
        if True not in direct_support.values():
            unknown_modes = tuple(mechanism for mechanism, available in direct_support.items() if available is None)
            if unknown_modes:
                direct_support.update(_probe_anthropic_values(create, model, unknown_modes))
        if low is None:
            low = _probe_anthropic_values(create, model, (_ANTHROPIC_LOW,)).get(_ANTHROPIC_LOW)
    except Exception as exc:
        _log_anthropic_probe_failure(exc, endpoint="Messages")

    direct = next((mechanism for mechanism, available in direct_support.items() if available), "")
    if direct or low:
        return ReasoningCapability.supported_controls(direct=direct, low=_ANTHROPIC_LOW if low else "")
    if low is False and all(available is False for available in direct_support.values()):
        return ReasoningCapability.unsupported()
    return ReasoningCapability.indeterminate()


def anthropic_reasoning_patch(
    capability: ReasoningCapability,
    intent: ReasoningIntent,
) -> ReasoningRequestPatch | None:
    if intent is ReasoningIntent.INHERIT:
        return None
    if intent is ReasoningIntent.PREFER_LOW and capability.low_mechanism == _ANTHROPIC_LOW:
        return ReasoningRequestPatch(_ANTHROPIC_LOW, _anthropic_control_fields(_ANTHROPIC_LOW), {})
    mechanism = capability.direct_mechanism
    if mechanism in _ANTHROPIC_THINKING_MODES:
        return ReasoningRequestPatch(mechanism, _anthropic_control_fields(mechanism), {})
    return None


def _anthropic_support(section: object, name: str) -> bool | None:
    support = _value(_value(section, name), "supported")
    return support if isinstance(support, bool) else None


def _anthropic_control_fields(mechanism: str, value: str | None = None) -> dict[str, object]:
    if mechanism == _ANTHROPIC_LOW:
        return {"output_config": {"effort": value or "low"}}
    return {"thinking": {"type": value or _ANTHROPIC_THINKING_MODES[mechanism]}}


def _probe_anthropic_values(create: Callable[..., object], model: str, mechanisms: tuple[str, ...]) -> dict[str, bool]:
    """Try one invalid value per field, then only the still-unknown legal values."""
    kwargs = {"model": model, "max_tokens": 8, "messages": _PROBE_MESSAGES}
    first = mechanisms[0]
    try:
        create(**kwargs, **_anthropic_control_fields(first, _ANTHROPIC_PROBE_VALUE))
    except Exception as exc:
        matched_param, error = _anthropic_control_error(exc, first)
        if not matched_param and not error:
            raise
        if any(word in error for word in _ANTHROPIC_UNKNOWN_FIELDS):
            return dict.fromkeys(mechanisms, False)
        if not any(word in error for word in _ANTHROPIC_VALUE_VALIDATION):
            if any(word in error for word in _ANTHROPIC_REJECTION_WORDS):
                return dict.fromkeys(mechanisms, False)
            return {}
    else:
        return {}

    results: dict[str, bool] = {}
    for mechanism in mechanisms:
        try:
            response = create(**kwargs, **_anthropic_control_fields(mechanism))
        except Exception as exc:
            if not is_anthropic_reasoning_rejection(exc, mechanism):
                raise
            results[mechanism] = False
        else:
            content = _value(response, "content")
            if not isinstance(content, (list, tuple)):
                continue
            has_thinking = any(_value(block, "type") in ("thinking", "redacted_thinking") for block in content)
            results[mechanism] = mechanism == _ANTHROPIC_LOW or not has_thinking
            if results[mechanism]:
                break
    return results


def _anthropic_control_error(exc: Exception, mechanism: str) -> tuple[bool, str]:
    if getattr(exc, "status_code", None) not in (400, 422):
        return False, ""
    is_thinking = mechanism in _ANTHROPIC_THINKING_MODES
    if not is_thinking and mechanism != _ANTHROPIC_LOW:
        return False, ""

    body = _value(exc, "body")
    detail = _value(body, "error") or body
    parameter = _value(exc, "param") or _value(detail, "param")
    message = _value(detail, "message")
    error = message.lower() if isinstance(message, str) else _exception_text(exc)
    if isinstance(parameter, str) and parameter:
        expected = r"thinking(?:\.type(?:\.\w+)?)?" if is_thinking else r"(?:output_config\.)?effort"
        matched = bool(re.fullmatch(expected, parameter.strip().lower()))
        return matched, error if matched else ""

    if _ANTHROPIC_MODEL_ERROR.match(error):
        return False, ""
    if mechanism == _ANTHROPIC_LOW:
        # output_config is also used for native schemas. A container-only error
        # does not establish that effort specifically was rejected.
        if _ANTHROPIC_SCHEMA_ERROR.search(error):
            return False, ""
        return False, error if _ANTHROPIC_EFFORT_ERROR.search(error) else ""
    return False, error if _ANTHROPIC_THINKING_ERROR.search(error) else ""


def is_anthropic_reasoning_rejection(exc: Exception, mechanism: str) -> bool:
    matched_param, error = _anthropic_control_error(exc, mechanism)
    return matched_param or any(word in error for word in _ANTHROPIC_REJECTION_WORDS)


def _log_anthropic_probe_failure(exc: Exception, *, endpoint: str) -> None:
    logger.debug(
        "Anthropic %s 思考能力探测中断: error=%s, status=%s",
        endpoint,
        type(exc).__name__,
        getattr(exc, "status_code", None),
    )


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
    "anthropic_reasoning_patch",
    "detect_anthropic_reasoning_capability",
    "detect_openai_reasoning_capability",
    "is_anthropic_reasoning_rejection",
    "is_openai_reasoning_rejection",
    "openai_reasoning_patch",
]
