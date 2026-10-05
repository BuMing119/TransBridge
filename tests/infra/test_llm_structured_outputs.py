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
    ensure_openai_responses_structured_output_completion,
    extract_structured_output_directive,
    openai_responses_text_config,
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
    openai = openai_responses_text_config(output_schema)
    anthropic = anthropic_output_config(output_schema)

    assert openai == {"format": {"type": "json_schema", "name": "translation_results", "schema": output_schema.schema}}
    assert anthropic == {"format": {"type": "json_schema", "schema": output_schema.schema}}
    openai["format"]["schema"]["properties"].clear()
    assert output_schema.schema["properties"] == {"answer": {"type": "string"}}


def test_validate_structured_output_returns_original_text(output_schema: LlmOutputSchema) -> None:
    raw = '{\n  "answer": "译文"\n}'
    assert validate_structured_output(raw, output_schema) == raw


@pytest.mark.parametrize("label", ["json", "JSON", ""])
def test_single_complete_json_fence_is_unwrapped_after_schema_validation(
    label: str, output_schema: LlmOutputSchema, caplog
) -> None:
    raw = f'\n```{label}\r\n{{"answer":"private translation"}}\r\n```\n'

    assert validate_structured_output(raw, output_schema) == '{"answer":"private translation"}'
    assert "recovered from a Markdown JSON code block" in caplog.text
    assert "private translation" not in caplog.text


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"answer":"ok"}\n``` trailing explanation',
        'prefix\n```json\n{"answer":"ok"}\n```',
        '```json\n{"answer":"ok"}\n```\n```json\n{"answer":"ok"}\n```',
        '```python\n{"answer":"ok"}\n```',
        '```json\n{"answer":"ok"}',
        '```json\n{"answer":7}\n```',
    ],
)
def test_invalid_or_nonconforming_fence_is_not_recovered(raw: str, output_schema: LlmOutputSchema) -> None:
    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        validate_structured_output(raw, output_schema)


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
    assert caught.value.raw_response == raw


def test_schema_failure_records_unexpected_fields_without_values(output_schema: LlmOutputSchema) -> None:
    raw = '{"answer":"ok", "reason":"private explanation", "confidence":1}'
    with pytest.raises(LlmStructuredOutputInvalidResponseError) as caught:
        validate_structured_output(raw, output_schema)
    assert caught.value.raw_response == raw
    assert caught.value.validation_details == {
        "schema": output_schema.name,
        "path": "<root>",
        "validator": "additionalProperties",
        "unexpected_fields": ["confidence", "reason"],
        "errors": [
            {
                "path": "<root>",
                "validator": "additionalProperties",
                "unexpected_fields": ["confidence", "reason"],
            }
        ],
    }
    assert "private explanation" not in str(caught.value)


def test_schema_failure_prioritizes_missing_translation_and_keeps_other_errors() -> None:
    from transbridge.ai_translator.structured_schemas import PROOFREAD_OUTPUT_SCHEMA

    raw = (
        '{"results":[{"entry_key":{"namespace":"test","local_key":"entry"},'
        '"original":"private source","current_translation":"private translation"}]}'
    )
    with pytest.raises(LlmStructuredOutputInvalidResponseError) as caught:
        validate_structured_output(raw, PROOFREAD_OUTPUT_SCHEMA)
    details = caught.value.validation_details
    assert details["validator"] == "required"
    assert details["path"] == "results/0"
    assert details["missing_fields"] == ["final_translation"]
    assert {error["validator"] for error in details["errors"]} == {"required", "additionalProperties"}
    assert details["errors"][-1]["unexpected_fields"] == ["current_translation", "original"]
    assert "private source" not in repr(details)
    assert "private translation" not in repr(details)


@pytest.mark.parametrize("status", [None, "in_progress", "failed"])
def test_openai_invalid_statuses_are_classified(status) -> None:
    with pytest.raises(LlmStructuredOutputInvalidResponseError):
        ensure_openai_responses_structured_output_completion(status=status)


def test_openai_refusal_and_truncation_are_classified() -> None:
    with pytest.raises(LlmStructuredOutputRefusalError):
        ensure_openai_responses_structured_output_completion(status="completed", refusal="cannot comply")
    with pytest.raises(LlmStructuredOutputTruncatedError):
        ensure_openai_responses_structured_output_completion(status="incomplete", incomplete_reason="max_output_tokens")
    ensure_openai_responses_structured_output_completion(status="completed")


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
        ("openai", "Unknown parameter: text.format.json_schema"),
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
