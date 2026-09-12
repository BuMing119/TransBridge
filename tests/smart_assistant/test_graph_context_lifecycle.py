from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.smart_assistant.checkpoint_manager import CheckpointManager
from transbridge.smart_assistant.graph_executor import GraphExecutor
from transbridge.smart_assistant.graph_types import ActionNode, ConditionNode, EdgeSpec, GraphSpec
from transbridge.smart_assistant.guardrails.base import GuardResult
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.types import ExecutionContext


class Guard:
    def before_execute(self, *_args):
        return GuardResult(allowed=True)

    def after_execute(self, *_args):
        return GuardResult(allowed=True)


@pytest.fixture
def manager():
    TaskManager.reset()
    yield TaskManager()
    TaskManager.reset()


def test_graph_preserves_captured_request_and_mutation_target(tmp_path, manager):
    request = RequestContext("owner", session_id="session", project_id="project", variant_id="variant")
    original_collection = []
    app = SimpleNamespace(collection=original_collection, active_version_identity=("project", "variant"))
    captured = ExecutionContext(app_context=app, request_context=request, owner_id="owner", task_manager=manager)
    app.collection = []
    app.active_version_identity = ("other-project", "other-variant")

    def inspect(_args, ctx):
        assert ctx.app_context is app
        assert ctx._target_collection is original_collection
        assert ctx._target_version_identity == ("project", "variant")
        return {"success": True, "data": task_metadata(ctx, {})}

    registry = SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(execute=inspect, permission="write"))
    executor = GraphExecutor(registry, captured, [Guard()], checkpoint_manager=CheckpointManager(tmp_path))
    try:
        result = executor._run_single({"id": 1, "tool": "inspect"})
    finally:
        executor.shutdown()
    assert result.success
    assert result.data == {
        "owner_id": "owner",
        "session_id": "session",
        "project_id": "project",
        "variant_id": "variant",
        "entrypoint": "smart-assistant",
    }
    assert captured.request_context is request


def test_cancelled_background_job_stops_graph_before_failure_recovery_branch(tmp_path, manager):
    calls = []

    def produce(_args, _ctx):
        task_id = manager.register()
        manager.start_thread(task_id, lambda: manager.cancel(task_id))
        return {"success": True, "data": {"task_id": task_id}}

    def fallback(_args, _ctx):
        calls.append("fallback")
        return {"success": True}

    registry = SimpleNamespace(
        get=lambda name, **_kwargs: SimpleNamespace(
            execute={"produce": produce, "fallback": fallback}[name], is_long_running=name == "produce"
        )
    )
    executor = GraphExecutor(registry, SimpleNamespace(), [Guard()], checkpoint_manager=CheckpointManager(tmp_path))
    graph = GraphSpec(
        "cancelled-branch",
        nodes=[
            ActionNode("produce", "action", tool="produce"),
            ConditionNode("recover", "condition", condition="True", true_node="fallback", false_node="fallback"),
            ActionNode("fallback", "action", tool="fallback"),
        ],
        edges=[EdgeSpec("produce", "recover")],
        entry_node="produce",
    )
    try:
        results = executor.execute_graph(graph)
    finally:
        executor.cancel()
        executor.shutdown()
    assert len(results) == 1
    assert results[0].data["status"] == "cancelled"
    assert not results[0].success
    assert calls == []


def test_cancel_before_graph_entry_cannot_be_erased_by_execution(tmp_path, manager):
    calls = []

    def write(_args, _ctx):
        calls.append("write")
        return {"success": True}

    registry = SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(execute=write, permission="write"))
    executor = GraphExecutor(
        registry, SimpleNamespace(), [Guard()], checkpoint_manager=CheckpointManager(tmp_path / "cancelled")
    )
    try:
        executor.cancel()
        assert executor.execute([{"id": 1, "tool": "write"}]) == []
        assert executor.execute([{"id": 1, "tool": "write"}]) == []
        assert calls == []
    finally:
        executor.shutdown()

    fresh = GraphExecutor(
        registry, SimpleNamespace(), [Guard()], checkpoint_manager=CheckpointManager(tmp_path / "fresh")
    )
    try:
        assert fresh.execute([{"id": 1, "tool": "write"}])[0].success
        assert calls == ["write"]
    finally:
        fresh.shutdown()
