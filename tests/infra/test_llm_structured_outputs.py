from __future__ import annotations

import pytest

from transbridge.infra.llm_client import AnthropicClient, OpenAICompatibleClient
from transbridge.infra.llm_structured_outputs import (
    STRUCTURED_OUTPUT_METADATA_KEY,
    LlmOutputSchema,
    LlmStructuredOutputInvalidResponseError,
    LlmStructuredOutputRefusalError,
    LlmStructuredOutputTruncatedError,
    LlmStructuredOutputUnsupportedError,
    anthropic_output_config,
    attach_structured_output_directive,
    ensure_anthropic_structured_output_completion,
    ensure_openai_structured_output_completion,
    extract_structured_output_directive,
    openai_response_format,
    raise_if_structured_output_unsupported,
    validate_structured_output,
)


@pytest.fixture
def output_schema() -> LlmOutputSchema:
    return LlmOutputSchema(
        "translation_results",
        {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )


def test_output_schema_is_immutable_and_returns_independent_schema() -> None:
    source = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "additionalProperties": False,
    }
    output_schema = LlmOutputSchema("stable_name-1", source)

    source["properties"]["answer"]["type"] = "integer"
    exposed = output_schema.schema
    exposed["properties"]["answer"]["type"] = "boolean"

    assert output_schema.schema["properties"]["answer"]["type"] == "string"
    with pytest.raises(AttributeError):
        output_schema.name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("name", ["", "has space", "dot.name", "x" * 65, 123])
def test_output_schema_rejects_invalid_provider_name(name) -> None:
    with pytest.raises(ValueError, match="schema name"):
        LlmOutputSchema(name, {"type": "object", "additionalProperties": False})


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        ({"type": "array", "items": {}}, "root"),
        ({"type": "object"}, "additionalProperties"),
        ({"type": "object", "additionalProperties": True}, "additionalProperties"),
        ({"type": "object", "additionalProperties": False, "required": "answer"}, "Draft 2020-12"),
    ],
)
def test_output_schema_rejects_invalid_or_non_strict_root(schema, message) -> None:
    with pytest.raises(ValueError, match=message):
        LlmOutputSchema("result", schema)


def test_directive_attach_extract_is_unique_and_non_mutating(output_schema: LlmOutputSchema) -> None:
    original = {"role": "system", "content": "Return JSON"}
    attached = attach_structured_output_directive(original, output_schema)
    messages = [attached, {"role": "user", "content": "Translate"}]

    clean, extracted = extract_structured_output_directive(messages)

    assert STRUCTURED_OUTPUT_METADATA_KEY not in original
    assert STRUCTURED_OUTPUT_METADATA_KEY in attached
    assert clean == [original, {"role": "user", "content": "Translate"}]
    assert messages[0] is attached
    assert extracted == output_schema


def test_directive_rejects_existing_malformed_and_duplicate_metadata(output_schema: LlmOutputSchema) -> None:
    attached = attach_structured_output_directive({"role": "system", "content": "x"}, output_schema)
    with pytest.raises(ValueError, match="already contains"):
        attach_structured_output_directive(attached, output_schema)
    with pytest.raises(ValueError, match="Malformed"):
        extract_structured_output_directive([{"role": "user", STRUCTURED_OUTPUT_METADATA_KEY: {"name": "x"}}])
    with pytest.raises(ValueError, match="more than one"):
        extract_structured_output_directive([attached, attached])


def test_provider_options_have_native_shapes_and_fresh_schema(output_schema: LlmOutputSchema) -> None:
    openai = openai_response_format(output_schema)
    anthropic = anthropic_output_config(output_schema)

    assert openai == {
        "type": "json_schema",
        "json_schema": {"name": "translation_results", "schema": output_schema.schema, "strict": True},
    }
    assert anthropic == {"format": {"type": "json_schema", "schema": output_schema.schema}}
    openai["json_schema"]["schema"]["properties"].clear()
    assert output_schema.schema["properties"] == {"answer": {"type": "string"}}


def test_validate_structured_output_returns_original_text(output_schema: LlmOutputSchema) -> None:
    raw = '{\n  "answer": "译文"\n}'
    assert validate_structured_output(raw, output_schema) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "not-json secret-translation",
        '[{"answer":"secret-translation"}]',
        '{"answer": 7, "private": "secret-translation"}',
    ],
)
def test_invalid_response_errors_do_not_echo_complete_response(raw: str, output_schema: LlmOutputSchema) -> None:
    with pytest.raises(LlmStructuredOutputInvalidResponseError) as caught:
        validate_structured_output(raw, output_schema)

    assert raw not in str(caught.value)
    assert "secret-translation" not in str(caught.value)


@pytest.mark.parametrize("finish_reason", [None, "content_filter", "tool_calls"])
def test_openai_invalid_finish_reasons_are_classified(finish_reason) -> None:
    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        ensure_openai_structured_output_completion(finish_reason=finish_reason)


def test_openai_refusal_and_truncation_are_classified() -> None:
    with pytest.raises(LlmStructuredOutputRefusalError):
        ensure_openai_structured_output_completion(finish_reason="stop", refusal="cannot comply")
    with pytest.raises(LlmStructuredOutputTruncatedError):
        ensure_openai_structured_output_completion(finish_reason="length")
    ensure_openai_structured_output_completion(finish_reason="stop")


@pytest.mark.parametrize("stop_reason", [None, "stop_sequence", "tool_use"])
def test_anthropic_invalid_stop_reasons_are_classified(stop_reason) -> None:
    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        ensure_anthropic_structured_output_completion(stop_reason=stop_reason)


def test_anthropic_refusal_and_truncation_are_classified() -> None:
    with pytest.raises(LlmStructuredOutputRefusalError):
        ensure_anthropic_structured_output_completion(stop_reason="refusal")
    with pytest.raises(LlmStructuredOutputTruncatedError):
        ensure_anthropic_structured_output_completion(stop_reason="max_tokens")
    ensure_anthropic_structured_output_completion(stop_reason="end_turn")


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        ("openai", "Unknown parameter: response_format.json_schema"),
        ("anthropic", "output_config is not supported"),
    ],
)
def test_explicit_provider_rejection_is_unsupported_and_preserves_cause(provider, message) -> None:
    class ProviderError(Exception):
        status_code = 400

        def __init__(self, error_message: str) -> None:
            super().__init__(error_message)
            self.message = error_message

    cause = ProviderError(message)
    with pytest.raises(LlmStructuredOutputUnsupportedError) as caught:
        raise_if_structured_output_unsupported(cause, provider=provider)

    assert caught.value.__cause__ is cause
    assert message not in str(caught.value)


def test_unrelated_provider_error_is_not_reclassified() -> None:
    cause = RuntimeError("connection failed")
    cause.status_code = 500  # type: ignore[attr-defined]
    assert raise_if_structured_output_unsupported(cause, provider="openai") is None


@pytest.mark.parametrize("client_type", [OpenAICompatibleClient, AnthropicClient])
def test_function_calling_rejects_structured_output_directive(client_type, output_schema) -> None:
    client = object.__new__(client_type)
    messages = [
        attach_structured_output_directive(
            {"role": "user", "content": "Translate"},
            output_schema,
        )
    ]

    with pytest.raises(ValueError, match="cannot be combined"):
        client.chat_stream_with_tools(messages, 100, [], lambda _chunk: None)
