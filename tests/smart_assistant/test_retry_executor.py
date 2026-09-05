from __future__ import annotations

import json

from transbridge.application.security.redaction import SecretRedactor
from transbridge.smart_assistant.reflexion import RetryHandler, ToolRetryExecutor
from transbridge.smart_assistant.tools import ToolResult


class _RepairClient:
    def __init__(self, adjusted_args: dict) -> None:
        self.adjusted_args = adjusted_args
        self.prompts: list[str] = []

    def chat(self, messages, max_tokens):
        assert max_tokens == 256
        self.prompts.append(messages[0]["content"])
        return json.dumps({"retry": True, "adjusted_args": self.adjusted_args, "reason": "repair"})


def test_failed_result_is_repaired_with_original_args_and_retried() -> None:
    client = _RepairClient({"query": "fixed"})
    runner = ToolRetryExecutor(RetryHandler(client))
    step = {
        "tool": "lookup",
        "args": {"query": "bad", "entry_key": "BOOK:42", "api_key": "secret-value"},
    }
    calls: list[dict] = []

    def invoke(args):
        calls.append(args)
        if args["query"] == "bad":
            return ToolResult.fail("query is invalid", error_category="input", error_code="INVALID_QUERY")
        return ToolResult.ok("found", data={"value": 1})

    outcome = runner.execute(
        step,
        invoke,
        retry_allowed=True,
        tool_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entry_key": {"type": "string"},
                "api_key": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    assert outcome.result.success
    assert outcome.attempts == 2
    assert calls == [
        {"query": "bad", "entry_key": "BOOK:42", "api_key": "secret-value"},
        {"query": "fixed", "api_key": "secret-value"},
    ]
    assert step["args"] == {"query": "bad", "entry_key": "BOOK:42", "api_key": "secret-value"}
    assert "secret-value" not in client.prompts[0]
    assert SecretRedactor.REDACTED in client.prompts[0]
    assert "BOOK:42" in client.prompts[0]
    assert "INVALID_QUERY" in client.prompts[0]
    assert '"required": ["query"]' in client.prompts[0]
    assert outcome.result.execution_meta == {"attempt": 2, "retry_count": 1}


def test_schema_rejected_sensitive_field_can_be_removed() -> None:
    client = _RepairClient({"query": "fixed"})
    runner = ToolRetryExecutor(RetryHandler(client))
    calls: list[dict] = []

    def invoke(args):
        calls.append(args)
        if "api_key" in args:
            return ToolResult.fail(
                "unknown field",
                data={
                    "json_pointer": "/api_key",
                    "validation_issues": [
                        {
                            "path": "/api_key",
                            "schema_path": "/additionalProperties",
                            "keyword": "additionalProperties",
                            "code": "UNKNOWN_FIELD",
                            "expected": False,
                            "actual_type": "string",
                            "message": "未声明的参数字段: api_key",
                        }
                    ],
                },
                error_category="input",
                error_code="ARGUMENT_SCHEMA_INVALID",
                recovery_action="adjust_arguments",
            )
        return ToolResult.ok("found")

    outcome = runner.execute(
        {"tool": "lookup", "args": {"query": "bad", "api_key": "secret-value"}},
        invoke,
        retry_allowed=True,
        tool_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    assert outcome.result.success
    assert outcome.attempts == 2
    assert calls == [
        {"query": "bad", "api_key": "secret-value"},
        {"query": "fixed"},
    ]
    assert "secret-value" not in client.prompts[0]
    assert '"code": "UNKNOWN_FIELD"' in client.prompts[0]


def test_analyzer_cannot_introduce_a_new_sensitive_value() -> None:
    client = _RepairClient({"query": "fixed", "api_key": "model-invented-secret"})
    runner = ToolRetryExecutor(RetryHandler(client))
    calls: list[dict] = []

    def invoke(args):
        calls.append(args)
        if args["query"] == "bad":
            return ToolResult.fail("query is invalid", error_category="input", error_code="INVALID_QUERY")
        return ToolResult.ok("found")

    outcome = runner.execute(
        {"tool": "lookup", "args": {"query": "bad"}},
        invoke,
        retry_allowed=True,
        tool_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "api_key": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    assert outcome.result.success
    assert calls == [{"query": "bad"}, {"query": "fixed"}]


def test_analyzer_receives_structured_failure_details() -> None:
    client = _RepairClient({"query": "fixed"})
    runner = ToolRetryExecutor(RetryHandler(client))

    outcome = runner.execute(
        {"tool": "lookup", "args": {"query": 42}},
        lambda _args: ToolResult.fail(
            "query must be a string",
            data={
                "json_pointer": "/query",
                "validation_issues": [
                    {
                        "path": "/query",
                        "schema_path": "/properties/query/type",
                        "keyword": "type",
                        "code": "TYPE_MISMATCH",
                        "expected": "string",
                        "actual_type": "integer",
                        "message": "query must be a string",
                    }
                ],
            },
            error_category="input",
            error_code="ARGUMENT_SCHEMA_INVALID",
            recovery_action="adjust_arguments",
        ),
        retry_allowed=True,
    )

    assert not outcome.result.success
    assert '"json_pointer": "/query"' in client.prompts[0]
    assert '"code": "TYPE_MISMATCH"' in client.prompts[0]
    assert '"expected": "string"' in client.prompts[0]
    assert '"actual_type": "integer"' in client.prompts[0]
    assert '"error_category": "input"' in client.prompts[0]
    assert '"recovery_action": "adjust_arguments"' in client.prompts[0]


def test_transient_read_failure_retries_same_args_without_analysis_client() -> None:
    runner = ToolRetryExecutor(RetryHandler())
    calls = 0

    def invoke(args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolResult.fail("request timeout", error_category="network", error_code="TIMEOUT")
        return ToolResult.ok("ok", data=args)

    outcome = runner.execute({"tool": "lookup", "args": {"page": 1}}, invoke, retry_allowed=True)

    assert outcome.result.success
    assert calls == 2
    assert outcome.step["args"] == {"page": 1}


def test_non_retryable_failure_stops_after_first_attempt() -> None:
    client = _RepairClient({"value": 2})
    runner = ToolRetryExecutor(RetryHandler(client))
    calls = 0

    def invoke(_args):
        nonlocal calls
        calls += 1
        return ToolResult.fail("API key missing", error_category="config", error_code="API_KEY_MISSING")

    outcome = runner.execute({"tool": "lookup", "args": {"value": 1}}, invoke, retry_allowed=True)

    assert not outcome.result.success
    assert outcome.attempts == 1
    assert calls == 1
    assert client.prompts == []


def test_retry_budget_is_bounded_to_three_retries() -> None:
    client = _RepairClient({"query": "still-bad"})
    runner = ToolRetryExecutor(RetryHandler(client))
    calls = 0
    retries: list[int] = []

    def invoke(_args):
        nonlocal calls
        calls += 1
        return ToolResult.fail("invalid query", error_category="input")

    outcome = runner.execute(
        {"tool": "lookup", "args": {"query": "bad"}},
        invoke,
        retry_allowed=True,
        on_retry=lambda next_attempt, _max_attempts, _result: retries.append(next_attempt),
    )

    assert not outcome.result.success
    assert outcome.attempts == 4
    assert calls == 4
    assert retries == [2, 3, 4]
    assert outcome.result.execution_meta == {"attempt": 4, "retry_count": 3}


def test_disabled_retry_policy_never_invokes_analyzer() -> None:
    client = _RepairClient({"query": "fixed"})
    runner = ToolRetryExecutor(RetryHandler(client))
    calls = 0

    def invoke(_args):
        nonlocal calls
        calls += 1
        return ToolResult.fail("invalid query", error_category="input")

    outcome = runner.execute({"tool": "write", "args": {}}, invoke, retry_allowed=False)

    assert not outcome.result.success
    assert calls == 1
    assert client.prompts == []


def test_cancellation_stops_before_invocation() -> None:
    calls = 0

    def invoke(_args):
        nonlocal calls
        calls += 1
        return ToolResult.ok()

    outcome = ToolRetryExecutor(RetryHandler()).execute(
        {"tool": "lookup", "args": {}},
        invoke,
        retry_allowed=True,
        cancelled=lambda: True,
    )

    assert not outcome.result.success
    assert outcome.result.error_code == "TOOL_CALL_CANCELLED"
    assert calls == 0
