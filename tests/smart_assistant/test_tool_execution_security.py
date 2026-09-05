from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.smart_assistant.guardrails import (
    InputValidationGuard,
    OutputValidationGuard,
    PermissionGuard,
)
from transbridge.smart_assistant.reflexion import RetryHandler
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools import ToolResult


class _Conversation:
    def __init__(self) -> None:
        self.display: list[str] = []
        self.structured: list[dict] = []
        self.tool_results: list[dict] = []

    def add_observation(self, tool_name: str, value: str) -> None:
        self.display.append(value)

    def add_structured_observation(self, tool_name: str, value: dict) -> None:
        self.structured.append(value)

    def add_tool_result(self, tool_call_id: str, tool_name: str, value: dict, **metadata) -> None:
        self.tool_results.append({"tool_call_id": tool_call_id, "tool_name": tool_name, "value": value, **metadata})


@pytest.fixture
def write_tool():
    name = f"contract_write_{uuid4().hex}"
    effects = {"count": 0}

    def execute(args, ctx):
        effects["count"] += 1
        return ToolResult.ok("written", data={"value": args["value"]})

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="write",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=execute,
        permission="write",
        require_confirmation=True,
    )
    ToolRegistry.register(spec, namespace="contract_handler")
    try:
        yield spec, effects
    finally:
        ToolRegistry._namespaced_tools["contract_handler"].pop(name, None)


def _handler(ctx, callback) -> ToolExecutionHandler:
    handler = ToolExecutionHandler(
        ctx,
        _Conversation(),
        on_confirm_permission=callback,
    )
    handler._middlewares = [
        PermissionGuard(),
        InputValidationGuard(),
        OutputValidationGuard(),
    ]
    return handler


def test_approved_confirmation_allows_exactly_one_real_side_effect(write_tool) -> None:
    spec, effects = write_tool
    handler = _handler(SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"), lambda *_: True)

    result = handler.execute_step({"tool": spec.name, "args": {"value": 1}})

    assert result is not None and result.success
    assert effects["count"] == 1
    observations = handler.get_structured_observations()
    assert observations[-1].result["data"] == {"value": 1}


def test_native_invocation_records_correlated_tool_result(write_tool) -> None:
    spec, _ = write_tool
    conversation = _Conversation()
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        conversation,
        on_confirm_permission=lambda *_: True,
    )
    handler._middlewares = [PermissionGuard(), InputValidationGuard(), OutputValidationGuard()]

    handler.execute_step({"tool": spec.name, "args": {"value": 1}, "tool_call_id": "call-1"})

    assert conversation.display == []
    assert conversation.tool_results[0]["tool_call_id"] == "call-1"
    assert conversation.tool_results[0]["is_error"] is False


def test_denied_confirmation_stops_before_side_effect(write_tool) -> None:
    spec, effects = write_tool
    handler = _handler(SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"), lambda *_: False)

    result = handler.execute_step({"tool": spec.name, "args": {"value": 1}})

    assert result is None
    assert effects["count"] == 0


def test_plan_context_change_during_confirmation_stops_before_side_effect(write_tool) -> None:
    spec, effects = write_tool
    ctx = SimpleNamespace(owner_id="gui-1", plan_hash="plan-1")

    def approve_then_change_context(*_):
        ctx.plan_hash = "plan-2"
        return True

    handler = _handler(ctx, approve_then_change_context)
    result = handler.execute_step({"tool": spec.name, "args": {"value": 1}})

    assert result is not None and not result.success
    assert effects["count"] == 0
    assert "请求已变化" in result.message


def test_argument_change_during_confirmation_stops_before_side_effect(write_tool) -> None:
    spec, effects = write_tool
    ctx = SimpleNamespace(owner_id="gui-1", plan_hash="plan-1")
    step = {"tool": spec.name, "args": {"value": 1}}

    def approve_then_change_request(*_):
        step["args"]["value"] = 2
        return True

    handler = _handler(ctx, approve_then_change_request)
    result = handler.execute_step(step)

    assert result is not None and not result.success
    assert effects["count"] == 0
    assert "请求已变化" in result.message


def test_missing_confirmation_channel_stops_before_side_effect(write_tool) -> None:
    spec, effects = write_tool
    handler = _handler(SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"), None)

    result = handler.execute_step({"tool": spec.name, "args": {"value": 1}})

    assert result is None
    assert effects["count"] == 0


def test_long_running_tool_reports_authoritative_job_and_suppresses_sync_completion() -> None:
    from transbridge.smart_assistant.tools.task_manager import TaskManager

    name = f"contract_long_{uuid4().hex}"
    manager = TaskManager()
    task_id = manager.register(metadata={"type": "contract"})
    started: list[tuple[str, str]] = []
    completed: list[bool] = []
    spec = ToolSpec(
        name=name,
        display_name=name,
        description="long",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _args, _ctx: ToolResult.ok("started", data={"task_id": task_id}),
        permission="read",
        is_long_running=True,
    )
    ToolRegistry.register(spec, namespace="contract_handler")
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        _Conversation(),
        on_task_started=lambda reported_task, run: started.append((reported_task, run)),
        on_step_completed=lambda: completed.append(True),
    )
    handler._middlewares = []
    try:
        result = handler.execute_step({"tool": name, "args": {}})
        status = manager.get_status(task_id)
        assert result is not None and result.success
        assert started == [(task_id, status["run_id"])]
        assert completed == []
    finally:
        manager.cancel(task_id)
        manager.cleanup(task_id)
        ToolRegistry._namespaced_tools["contract_handler"].pop(name, None)


def test_retry_cannot_reuse_confirmation_for_second_side_effect() -> None:
    name = f"contract_retry_{uuid4().hex}"
    effects = {"count": 0}

    def execute(args, ctx):
        effects["count"] += 1
        return ToolResult.fail("temporary write failure")

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="write then fail",
        parameters={},
        execute=execute,
        permission="write",
        require_confirmation=True,
    )
    ToolRegistry.register(spec, namespace="contract_retry")
    handler = _handler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        lambda *_: True,
    )
    try:
        result = handler.execute_step({"tool": name, "args": {}})
    finally:
        ToolRegistry._namespaced_tools["contract_retry"].pop(name, None)

    assert result is not None and not result.success
    assert effects["count"] == 1


def test_read_tool_failure_is_repaired_and_closes_native_call_once() -> None:
    name = f"contract_read_retry_{uuid4().hex}"
    calls: list[dict] = []
    completed: list[bool] = []
    conversation = _Conversation()

    def execute(args, _ctx):
        calls.append(dict(args))
        if args["value"] == 1:
            return ToolResult.fail("value is invalid", error_category="input", error_code="INVALID_VALUE")
        return ToolResult.ok("read", data={"value": args["value"]})

    class Client:
        def chat(self, _messages, max_tokens):
            assert max_tokens == 256
            return json.dumps({"retry": True, "adjusted_args": {"value": 2}, "reason": "repair"})

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="read retry",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=execute,
        permission="read",
    )
    ToolRegistry.register(spec, namespace="contract_retry")
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        conversation,
        retry_handler=RetryHandler(Client()),
        on_step_completed=lambda: completed.append(True),
    )
    handler._middlewares = []
    try:
        result = handler.execute_step({"tool": name, "args": {"value": 1}, "tool_call_id": "call-retry"})
    finally:
        ToolRegistry._namespaced_tools["contract_retry"].pop(name, None)

    assert result is not None and result.success
    assert calls == [{"value": 1}, {"value": 2}]
    assert completed == [True]
    assert len(conversation.tool_results) == 1
    assert conversation.tool_results[0]["tool_call_id"] == "call-retry"
    assert conversation.tool_results[0]["is_error"] is False


def test_schema_rejected_sensitive_field_is_removed_before_retry() -> None:
    name = f"contract_sensitive_unknown_retry_{uuid4().hex}"
    calls: list[dict] = []
    prompts: list[str] = []
    conversation = _Conversation()

    def execute(args, _ctx):
        calls.append(dict(args))
        return ToolResult.ok("read", data=args)

    class Client:
        def chat(self, messages, max_tokens):
            assert max_tokens == 256
            prompts.append(messages[0]["content"])
            return json.dumps({"retry": True, "adjusted_args": {"query": "dragon"}, "reason": "remove unknown"})

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="remove schema-rejected sensitive field",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=execute,
        permission="read",
    )
    ToolRegistry.register(spec, namespace="contract_retry")
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        conversation,
        retry_handler=RetryHandler(Client()),
    )
    handler._middlewares = [InputValidationGuard()]
    try:
        result = handler.execute_step({
            "tool": name,
            "args": {"query": "dragon", "api_key": "secret-value"},
            "tool_call_id": "call-sensitive-unknown",
        })
    finally:
        ToolRegistry._namespaced_tools["contract_retry"].pop(name, None)

    assert result is not None and result.success
    assert calls == [{"query": "dragon"}]
    assert len(prompts) == 1
    assert '"code": "UNKNOWN_FIELD"' in prompts[0]
    assert "secret-value" not in prompts[0]
    assert len(conversation.tool_results) == 1
    assert conversation.tool_results[0]["tool_call_id"] == "call-sensitive-unknown"


def test_exhausted_failure_closes_native_call_and_continues_react_once() -> None:
    name = f"contract_read_fail_{uuid4().hex}"
    completed: list[bool] = []
    conversation = _Conversation()
    spec = ToolSpec(
        name=name,
        display_name=name,
        description="read failure",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=lambda _args, _ctx: ToolResult.fail(
            "API key missing",
            error_category="config",
            error_code="API_KEY_MISSING",
        ),
        permission="read",
    )
    ToolRegistry.register(spec, namespace="contract_retry")
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        conversation,
        on_step_completed=lambda: completed.append(True),
    )
    handler._middlewares = []
    try:
        result = handler.execute_step({"tool": name, "args": {}, "tool_call_id": "call-fail"})
    finally:
        ToolRegistry._namespaced_tools["contract_retry"].pop(name, None)

    assert result is not None and not result.success
    assert completed == [True]
    assert len(conversation.tool_results) == 1
    assert conversation.tool_results[0]["tool_call_id"] == "call-fail"
    assert conversation.tool_results[0]["is_error"] is True
    assert conversation.tool_results[0]["value"]["result"]["error_code"] == "API_KEY_MISSING"


def test_detached_read_retry_defers_conversation_and_ui_finalization() -> None:
    name = f"contract_detached_retry_{uuid4().hex}"
    calls: list[dict] = []
    messages: list[str] = []
    conversation = _Conversation()

    def execute(args, _ctx):
        calls.append(dict(args))
        if args["value"] == 1:
            return ToolResult.fail("invalid value", error_category="input", error_code="INVALID_VALUE")
        return ToolResult.ok("read", data={"value": args["value"]})

    class Client:
        def chat(self, _messages, max_tokens):
            assert max_tokens == 256
            return json.dumps({"retry": True, "adjusted_args": {"value": 2}, "reason": "repair"})

    spec = ToolSpec(
        name=name,
        display_name=name,
        description="detached read retry",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        execute=execute,
        permission="read",
    )
    ToolRegistry.register(spec, namespace="contract_retry")
    handler = ToolExecutionHandler(
        SimpleNamespace(owner_id="gui-1", plan_hash="plan-1"),
        conversation,
        retry_handler=RetryHandler(Client()),
        on_system_message=messages.append,
    )
    handler._middlewares = []
    step = {"tool": name, "args": {"value": 1}, "tool_call_id": "call-detached"}
    try:
        completed = handler.execute_steps_detached([step])
        assert conversation.tool_results == []
        assert messages == []
        handler.complete_detached(completed)
    finally:
        ToolRegistry._namespaced_tools["contract_retry"].pop(name, None)

    assert calls == [{"value": 1}, {"value": 2}]
    assert len(conversation.tool_results) == 1
    assert conversation.tool_results[0]["tool_call_id"] == "call-detached"
    assert messages[0].startswith("[重试 2/4]")
    assert messages[1].startswith("[OK]")
