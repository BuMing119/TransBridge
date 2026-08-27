"""Provider-neutral contracts for native LLM Structured Outputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

STRUCTURED_OUTPUT_METADATA_KEY = "_transbridge_structured_output"

_SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNSUPPORTED_STATUS_CODES = frozenset({400, 422})
_UNSUPPORTED_MARKERS = (
    "not support",
    "unsupported",
    "unknown parameter",
    "unrecognized",
    "not permitted",
    "extra inputs",
)


class LlmStructuredOutputError(RuntimeError):
    """Base error for a native Structured Outputs request or response."""


class LlmStructuredOutputUnsupportedError(LlmStructuredOutputError):
    """The selected Provider endpoint does not support Structured Outputs."""


class LlmStructuredOutputRefusalError(LlmStructuredOutputError):
    """The Provider reported that the model refused the request."""


class LlmStructuredOutputTruncatedError(LlmStructuredOutputError):
    """The Provider stopped before a complete structured response was produced."""


class LlmStructuredOutputInvalidResponseError(LlmStructuredOutputError):
    """The Provider returned a response that cannot satisfy the requested schema."""


@dataclass(frozen=True, init=False)
class LlmOutputSchema:
    """An immutable, Provider-neutral named JSON object schema.

    The canonical JSON string prevents callers from mutating the stored schema
    through a reference retained after construction. The public ``schema``
    property returns a fresh object for the same reason.
    """

    name: str
    _schema_json: str

    def __init__(self, name: str, schema: dict[str, Any]) -> None:
        if not isinstance(name, str) or _SCHEMA_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError("Structured output schema name must contain 1-64 letters, digits, underscores, or hyphens")
        if not isinstance(schema, dict):
            raise TypeError("Structured output schema must be a JSON object")
        if schema.get("type") != "object":
            raise ValueError("Structured output schema root must have type=object")
        if schema.get("additionalProperties") is not False:
            raise ValueError("Structured output schema root must declare additionalProperties=false")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError("Structured output schema is not a valid Draft 2020-12 JSON Schema") from exc
        try:
            schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Structured output schema must contain only JSON-compatible values") from exc

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "_schema_json", schema_json)

    @property
    def schema(self) -> dict[str, Any]:
        """Return an independent JSON object suitable for a Provider request."""

        value = json.loads(self._schema_json)
        if not isinstance(value, dict):  # Guard the invariant if construction changes later.
            raise AssertionError("Stored structured output schema is not an object")
        return value


def attach_structured_output_directive(message: dict[str, Any], output_schema: LlmOutputSchema) -> dict[str, Any]:
    """Attach one internal schema directive without mutating ``message``."""

    if not isinstance(message, dict):
        raise TypeError("Structured output directives can only be attached to message objects")
    if not isinstance(output_schema, LlmOutputSchema):
        raise TypeError("output_schema must be an LlmOutputSchema")
    if STRUCTURED_OUTPUT_METADATA_KEY in message:
        raise ValueError("Message already contains a structured output directive")
    return {
        **message,
        STRUCTURED_OUTPUT_METADATA_KEY: {
            "name": output_schema.name,
            "schema": output_schema.schema,
        },
    }


def extract_structured_output_directive(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], LlmOutputSchema | None]:
    """Strip and validate the unique structured-output directive in ``messages``.

    Unlike prompt-cache hints, malformed Structured Outputs metadata is an
    error: silently downgrading would violate the native-output contract.
    Neither the input list nor its message objects are modified.
    """

    clean_messages: list[dict[str, Any]] = []
    directives: list[LlmOutputSchema] = []
    for message in messages:
        if not isinstance(message, dict):
            raise TypeError("LLM messages must be objects")
        clean_messages.append({key: value for key, value in message.items() if key != STRUCTURED_OUTPUT_METADATA_KEY})
        if STRUCTURED_OUTPUT_METADATA_KEY not in message:
            continue
        raw_directive = message[STRUCTURED_OUTPUT_METADATA_KEY]
        if not isinstance(raw_directive, dict) or set(raw_directive) != {"name", "schema"}:
            raise ValueError("Malformed structured output directive")
        directives.append(LlmOutputSchema(raw_directive["name"], raw_directive["schema"]))

    if len(directives) > 1:
        raise ValueError("Messages contain more than one structured output directive")
    return clean_messages, directives[0] if directives else None


def openai_response_format(output_schema: LlmOutputSchema) -> dict[str, Any]:
    """Build the OpenAI-compatible Chat Completions response format."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_schema.name,
            "schema": output_schema.schema,
            "strict": True,
        },
    }


def anthropic_output_config(output_schema: LlmOutputSchema) -> dict[str, Any]:
    """Build the Anthropic Messages native output configuration."""

    return {
        "format": {
            "type": "json_schema",
            "schema": output_schema.schema,
        }
    }


def validate_structured_output(raw_text: str, output_schema: LlmOutputSchema) -> str:
    """Validate a complete Provider response and preserve the text API result."""

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise LlmStructuredOutputInvalidResponseError("Structured output response was empty")
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LlmStructuredOutputInvalidResponseError(
            f"Structured output response was not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(value, dict):
        raise LlmStructuredOutputInvalidResponseError("Structured output response root was not an object")

    validator = Draft202012Validator(output_schema.schema)
    error = next(validator.iter_errors(value), None)
    if error is not None:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise LlmStructuredOutputInvalidResponseError(
            f"Structured output response failed schema validation at {location} ({error.validator})"
        )
    return raw_text


def ensure_openai_structured_output_completion(
    *,
    finish_reason: str | None,
    refusal: object | None = None,
) -> None:
    """Classify OpenAI refusal, truncation, and invalid completion states."""

    if refusal not in (None, ""):
        raise LlmStructuredOutputRefusalError("OpenAI structured output request was refused")
    if finish_reason == "length":
        raise LlmStructuredOutputTruncatedError("OpenAI structured output response was truncated")
    if finish_reason != "stop":
        raise LlmStructuredOutputInvalidResponseError("OpenAI structured output response had an invalid finish reason")


def ensure_anthropic_structured_output_completion(*, stop_reason: str | None) -> None:
    """Classify Anthropic refusal, truncation, and invalid completion states."""

    if stop_reason == "refusal":
        raise LlmStructuredOutputRefusalError("Anthropic structured output request was refused")
    if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
        raise LlmStructuredOutputTruncatedError("Anthropic structured output response was truncated")
    if stop_reason != "end_turn":
        raise LlmStructuredOutputInvalidResponseError("Anthropic structured output response had an invalid stop reason")


def raise_if_structured_output_unsupported(
    exc: Exception,
    *,
    provider: Literal["openai", "anthropic"],
) -> None:
    """Raise a classified error when a Provider clearly rejects native schema parameters."""

    if getattr(exc, "status_code", None) not in _UNSUPPORTED_STATUS_CODES:
        return
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) else str(exc)
    lowered = text.lower()
    parameter_markers = ("response_format", "json_schema") if provider == "openai" else ("output_config", "json_schema")
    if not any(marker in lowered for marker in parameter_markers):
        return
    if not any(marker in lowered for marker in _UNSUPPORTED_MARKERS):
        return
    raise LlmStructuredOutputUnsupportedError(
        f"{provider.title()} endpoint does not support native structured outputs"
    ) from exc


__all__ = [
    "STRUCTURED_OUTPUT_METADATA_KEY",
    "LlmOutputSchema",
    "LlmStructuredOutputError",
    "LlmStructuredOutputInvalidResponseError",
    "LlmStructuredOutputRefusalError",
    "LlmStructuredOutputTruncatedError",
    "LlmStructuredOutputUnsupportedError",
    "anthropic_output_config",
    "attach_structured_output_directive",
    "ensure_anthropic_structured_output_completion",
    "ensure_openai_structured_output_completion",
    "extract_structured_output_directive",
    "openai_response_format",
    "raise_if_structured_output_unsupported",
    "validate_structured_output",
]
