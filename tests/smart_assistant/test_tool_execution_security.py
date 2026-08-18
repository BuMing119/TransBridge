from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.smart_assistant.guardrails import (
    InputValidationGuard,
    OutputValidationGuard,
    PermissionGuard,
)
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools import ToolResult


class _Conversation:
    def __init__(self) -> None:
        self.display: list[str] = []
        self.structured: list[dict] = []

    def add_observation(self, tool_name: str, value: str) -> None:
        self.display.append(value)

    def add_structured_observation(self, tool_name: str, value: dict) -> None:
        self.structured.append(value)


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
