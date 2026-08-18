from __future__ import annotations

from datetime import UTC, datetime
import threading
import time

import pytest

from transbridge.application.tasks import (
    CheckpointExpectation,
    GraphWorkloadAdapter,
    GraphWorkloadState,
    JobSpec,
    JobState,
    OwnerRef,
    TaskRuntime,
)
from transbridge.smart_assistant.checkpoint_manager import CheckpointManager
from transbridge.smart_assistant.graph_executor import GraphCheckpointFault, GraphExecutor
from transbridge.smart_assistant.graph_types import (
    ActionNode,
    ConditionNode,
    EdgeSpec,
    GraphSpec,
    HumanConfirmNode,
    LoopNode,
)
from transbridge.smart_assistant.guardrails.base import GuardResult


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"run-{self.value}"


class Context:
    owner_id = "graph-owner"
    entrypoint = "agent"
    project_id = "project-1"
    variant_id = "variant-1"
    session_id = "session-1"
    run_id = "graph-run-1"
    input_fingerprint = "sha256:graph-input"


class Guard:
    def before_execute(self, step, context):
        return GuardResult(allowed=True)

    def after_execute(self, step, result, context):
        return GuardResult(allowed=True)


class Registry:
    def __init__(self, *, delay: float = 0) -> None:
        self.calls: list[str] = []
        self.delay = delay

    def get(self, name, namespace=None):
        registry = self

        class Spec:
            permission = "read"

            def execute(self, args, context):
                if registry.delay:
                    time.sleep(registry.delay)
                registry.calls.append(name)
                return {"success": True, "message": f"ran:{name}", "data": {"tool": name}}

        return Spec()


def linear_graph() -> GraphSpec:
    return GraphSpec(
        graph_id="linear-checkpoint",
        nodes=[
            ActionNode("a", "action", tool="tool-a"),
            ActionNode("b", "action", tool="tool-b"),
        ],
        edges=[EdgeSpec("a", "b")],
        entry_node="a",
    )


def executor(registry, manager, context=None) -> GraphExecutor:
    return GraphExecutor(registry, context or Context(), middlewares=[Guard()], checkpoint_manager=manager)


def test_frontier_result_resume_and_repeated_resume_do_not_repeat_completed_nodes(tmp_path):
    armed = {"value": True}

    def fault(stage, _path):
        if stage == "node_completed" and armed["value"]:
            armed["value"] = False
            raise GraphCheckpointFault("crash after node result became durable")

    registry = Registry()
    manager = CheckpointManager(tmp_path, fault_injector=fault)
    first = executor(registry, manager)
    with pytest.raises(GraphCheckpointFault):
        first.execute_graph(linear_graph())
    first.shutdown()
    assert registry.calls == ["tool-a"]

    second = executor(registry, manager)
    results = second.execute_graph(linear_graph())
    second.shutdown()
    assert registry.calls == ["tool-a", "tool-b"]
    assert [result.tool for result in results] == ["tool-a", "tool-b"]

    third = executor(registry, manager)
    repeated = third.execute_graph(linear_graph())
    third.shutdown()
    assert registry.calls == ["tool-a", "tool-b"]
    assert [result.tool for result in repeated] == ["tool-a", "tool-b"]


def test_pause_really_blocks_until_resume_without_losing_frontier(tmp_path):
    registry = Registry()
    value = executor(registry, CheckpointManager(tmp_path))
    value.pause()
    result_holder = []
    worker = threading.Thread(target=lambda: result_holder.extend(value.execute_graph(linear_graph())))
    worker.start()
    time.sleep(0.05)
    assert worker.is_alive()
    assert registry.calls == []

    value.resume()
    worker.join(2)
    value.shutdown()
    assert not worker.is_alive()
    assert [result.tool for result in result_holder] == ["tool-a", "tool-b"]


def test_hitl_result_is_durable_before_fault_and_resume_does_not_prompt_again(tmp_path):
    armed = {"value": True}

    def fault(stage, _path):
        if stage == "hitl_result" and armed["value"]:
            armed["value"] = False
            raise GraphCheckpointFault("crash after hitl")

    graph = GraphSpec(
        "hitl-checkpoint",
        [HumanConfirmNode("confirm", "human_confirm", prompt="continue?", choices=["yes"], default_choice="yes")],
        [],
        "confirm",
    )
    manager = CheckpointManager(tmp_path, fault_injector=fault)
    first = executor(Registry(), manager)
    prompts = []
    first.on_step_requires_confirmation(lambda *args: prompts.append(args))
    first.provide_decision("confirm", "yes")
    with pytest.raises(GraphCheckpointFault):
        first.execute_graph(graph)
    first.shutdown()
    assert len(prompts) == 1

    second = executor(Registry(), manager)
    second_prompts = []
    second.on_step_requires_confirmation(lambda *args: second_prompts.append(args))
    results = second.execute_graph(graph)
    second.shutdown()
    assert second_prompts == []
    assert results[0].data == {"decision": "yes"}


def test_branch_decision_is_durable_and_reused_after_fault(tmp_path):
    armed = {"value": True}

    def fault(stage, _path):
        if stage == "branch_decision" and armed["value"]:
            armed["value"] = False
            raise GraphCheckpointFault("crash after branch")

    graph = GraphSpec(
        "branch-checkpoint",
        [
            ConditionNode("condition", "condition", condition="True", true_node="yes", false_node="no"),
            ActionNode("yes", "action", tool="tool-yes"),
            ActionNode("no", "action", tool="tool-no"),
        ],
        [],
        "condition",
    )
    manager = CheckpointManager(tmp_path, fault_injector=fault)
    registry = Registry()
    first = executor(registry, manager)
    with pytest.raises(GraphCheckpointFault):
        first.execute_graph(graph)
    first.shutdown()

    second = executor(registry, manager)
    second._condition_evaluator.eval_condition = lambda *_args: False
    results = second.execute_graph(graph)
    second.shutdown()
    assert [result.tool for result in results] == ["tool-yes"]
    assert registry.calls == ["tool-yes"]


def test_loop_counter_resumes_after_last_durable_iteration(tmp_path):
    armed = {"value": True}

    def fault(stage, _path):
        if stage == "loop_iteration_completed" and armed["value"]:
            armed["value"] = False
            raise GraphCheckpointFault("crash after loop iteration")

    graph = GraphSpec(
        "loop-checkpoint",
        [
            LoopNode(
                "loop",
                "loop",
                sub_nodes=[ActionNode("body", "action", tool="tool-body")],
                max_iterations=2,
                exit_condition="",
            )
        ],
        [],
        "loop",
    )
    manager = CheckpointManager(tmp_path, fault_injector=fault)
    registry = Registry()
    first = executor(registry, manager)
    with pytest.raises(GraphCheckpointFault):
        first.execute_graph(graph)
    first.shutdown()

    second = executor(registry, manager)
    second.execute_graph(graph)
    second.shutdown()
    assert registry.calls == ["tool-body", "tool-body"]


def test_graph_workload_never_writes_task_runtime_terminal_state(tmp_path):
    tasks = TaskRuntime(id_generator=Ids(), clock=Clock())
    owner = OwnerRef(
        Context.owner_id,
        Context.entrypoint,
        project_id=Context.project_id,
        variant_id=Context.variant_id,
        session_id=Context.session_id,
    )
    ref = tasks.submit(JobSpec("graph", "graph:input", "sha256:graph-input"), owner).ref
    tasks.start(ref, owner)

    value = executor(Registry(), CheckpointManager(tmp_path))
    results = value.execute_graph(linear_graph())
    value.shutdown()

    assert len(results) == 2
    assert tasks.get(ref, owner).state is JobState.RUNNING
    tasks.complete(ref, owner)


def test_same_graph_independent_run_ids_never_share_checkpoint(tmp_path):
    class RunOne(Context):
        run_id = "independent-run-1"

    class RunTwo(Context):
        run_id = "independent-run-2"

    manager = CheckpointManager(tmp_path)
    first_registry = Registry()
    first = executor(first_registry, manager, RunOne())
    first.execute_graph(linear_graph())
    first.shutdown()

    second_registry = Registry()
    second = executor(second_registry, manager, RunTwo())
    second.execute_graph(linear_graph())
    second.shutdown()

    resumed_registry = Registry()
    resumed = executor(resumed_registry, manager, RunOne())
    resumed.execute_graph(linear_graph())
    resumed.shutdown()

    assert first_registry.calls == ["tool-a", "tool-b"]
    assert second_registry.calls == ["tool-a", "tool-b"]
    assert resumed_registry.calls == []


def test_legacy_missing_identity_is_isolated_per_execution(tmp_path):
    class LegacyContext:
        run_id = None
        owner_id = None
        entrypoint = None
        input_fingerprint = None

    manager = CheckpointManager(tmp_path)
    registry = Registry()
    first = executor(registry, manager, LegacyContext())
    first.execute_graph(linear_graph())
    first.shutdown()
    second = executor(registry, manager, LegacyContext())
    second.execute_graph(linear_graph())
    second.shutdown()
    assert registry.calls == ["tool-a", "tool-b", "tool-a", "tool-b"]


def test_graph_workload_adapter_consumes_identity_and_token_without_terminal_write(tmp_path):
    graph = linear_graph()
    registry = Registry()
    graph_executor = executor(registry, CheckpointManager(tmp_path))
    owner = OwnerRef(
        Context.owner_id,
        Context.entrypoint,
        project_id=Context.project_id,
        variant_id=Context.variant_id,
        session_id=Context.session_id,
    )
    identity = CheckpointExpectation(
        "adapter-run",
        owner,
        graph_executor.graph_spec_fingerprint(graph),
        Context.input_fingerprint,
    )
    adapter = GraphWorkloadAdapter(graph_executor, graph, identity)
    tasks = TaskRuntime(id_generator=Ids(), clock=Clock())
    ref = tasks.submit(JobSpec("graph", "graph:input", Context.input_fingerprint), owner).ref
    tasks.start(ref, owner)
    token = tasks.cancellation_token(ref, owner)

    outcome = adapter(token)

    assert outcome.state is GraphWorkloadState.COMPLETED
    assert outcome.run_id == "adapter-run"
    assert tasks.get(ref, owner).state is JobState.RUNNING
    tasks.cancel(ref, owner)
    cancelled = adapter(token)
    assert cancelled.state is GraphWorkloadState.CANCELLED
    assert tasks.get(ref, owner).state is JobState.CANCELLING
    graph_executor.shutdown()
