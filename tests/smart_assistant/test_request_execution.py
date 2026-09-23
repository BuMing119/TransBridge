"""Real request persistence plus common tool and TaskRuntime admission integration."""

from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import ItemKind, RequestItem, RequestStatus, UserRequest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.types import ExecutionContext, ToolResult


@pytest.fixture
def setup(tmp_path, monkeypatch):
    TaskManager.reset()
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-09-12T00:00:00Z"
    )
    assert services.gui_session_commands.create_and_activate("A", RequestContext("owner")).is_success
    session_id = services.session_lifecycle.active.aggregate.ref.identity.value
    context = RequestContext("owner", session_id=session_id)
    service = services.gui_session_commands.assistant_requests
    source = service.accept_input(context, "write", selection={})
    request = UserRequest(
        "request",
        session_id,
        "write",
        (RequestItem("write", "write", ItemKind.EXECUTION),),
        source_message_ids=(source["message_id"],),
        work_round_id=source["message_id"],
    )

    def apply(state):
        state["requests"] = [request.to_dict()]
        state["ingress"][0]["status"] = "applied"

    service.transact(context, apply)
    service.scheduler.activate(session_id, "view")
    selected = service.scheduler.select_next_turn(
        session_id, "view", (request,), allowed_tools=("request_test_write", "request_test_read")
    )
    gate = RequestExecutionGate(service, context, selected.admission)
    ctx = ExecutionContext(request_context=context, assistant_gate=gate, assistant_required=True)
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"default": {}})
    yield service, context, gate, ctx, TaskManager()
    router = getattr(service, "request_task_events", None)
    if router:
        router.close()
    TaskManager.reset()
    services.close()


def register(fn, *, long=False):
    spec = ToolSpec("request_test_write", "write", "write", {}, execute=fn, permission="write", is_long_running=long)
    ToolRegistry.register(spec)
    return spec


@pytest.mark.parametrize("transient", [False, True])
def test_terminal_receipt_retry_is_scoped_and_never_reexecutes_work(setup, monkeypatch, transient):
    service, context, gate, ctx, manager = setup
    start = Event()
    writes = []
    original = service.update_request
    failures = []
    recovered = False

    def save(*args, **kwargs):
        if getattr(kwargs.get("cause"), "operation", "") == "task.result" and not recovered:
            if not transient or not failures:
                failures.append("save failed")
                raise OSError("injected result save failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "update_request", save)

    def tool(args, execution_context):
        task_id = manager.register(metadata=task_metadata(execution_context, {}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            assert handle.execution.commit(handle.execution.ref.run_id, lambda: writes.append("once")).accepted

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    handle = manager.get_handle(result.data["task_id"])
    start.set()
    handle._thread.join(5)
    assert not handle._thread.is_alive()
    router = service.request_task_events
    assert failures and router.diagnostics
    assert router.has_pending(context.session_id, "request") is (not transient)
    assert not router.has_pending(context.session_id, "other-request")
    assert not router.has_pending("other-session", "request")
    if not transient:
        from transbridge.application.assistant_requests.models import RequestError

        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            gate._validate(gate.current_request(), "request_test_read")
        unrelated = replace(gate.current_request(), request_id="other-request", effects=(), dispatches=())
        other_gate = RequestExecutionGate(
            service, context, replace(gate.admission, request_id=unrelated.request_id), background=True
        )
        other_gate._validate(unrelated, "request_test_read")
    recovered = True

    def history_unavailable(*args, **kwargs):
        raise AssertionError("receipt retry must use its retained terminal snapshot")

    with monkeypatch.context() as unavailable:
        unavailable.setattr(manager.runtime, "get", history_unavailable)
        service.notify(context.session_id)
    assert not router.has_pending(context.session_id, "request")
    assert gate.current_request().effects[0].status == "succeeded"
    gate._validate(gate.current_request(), "request_test_read")
    router.retry_pending(context.session_id)
    assert writes == ["once"]
    assert len(gate.current_request().evidence) == 1


def test_saved_terminal_receipt_notification_failure_does_not_block_admission(setup, monkeypatch):
    service, context, gate, ctx, manager = setup
    start = Event()

    def tool(args, execution_context):
        task_id = manager.register(metadata=task_metadata(execution_context, {}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            assert handle.execution.commit(handle.execution.ref.run_id, lambda: None).accepted

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])

    def fail_notify(_session):
        raise RuntimeError("view destroyed")

    monkeypatch.setattr(service, "notify", fail_notify)
    start.set()
    manager.get_handle(result.data["task_id"])._thread.join(5)
    assert gate.current_request().effects[0].status == "succeeded"
    assert not service.request_task_events.has_pending(context.session_id, "request")
    assert not service.request_task_events.diagnostics
    gate._validate(gate.current_request(), "request_test_read")


def test_sync_write_is_admitted_persisted_and_current_turn_fenced(setup):
    service, context, gate, ctx, _ = setup
    writes = []
    spec = register(lambda args, ctx: (ctx.safe_mutate_wait(lambda: writes.append("done")), ToolResult.ok("done"))[1])
    outcome = execute_with_guardrails(spec, {}, ctx, middlewares=[])
    assert outcome.success and writes == ["done"]
    saved = gate.current_request()
    assert saved.status == RequestStatus.OPEN and len(saved.effects) == 1
    assert saved.effects[0].receipt and saved.evidence[0].reference
    service.scheduler.release(gate.admission)
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "TURN_LEASE_STALE"
    assert writes == ["done"]


def test_missing_or_stale_gate_and_forged_owner_perform_zero_writes(setup):
    service, context, gate, ctx, _ = setup
    writes = []
    spec = register(lambda args, ctx: writes.append("bad"))
    ctx.assistant_gate = None
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "TURN_LEASE_STALE"
    ctx.assistant_gate = gate
    assert (
        execute_with_guardrails(spec, {"request_id": "fake"}, ctx, middlewares=[]).error_code
        == "REQUEST_PROTOCOL_INVALID"
    )
    service.scheduler.activate(context.session_id, "other-view")
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "TURN_LEASE_STALE"
    assert not writes


def test_async_commit_records_original_session_after_view_changes(setup):
    service, context, gate, ctx, manager = setup
    start = Event()
    committed = []

    def tool(args, ctx):
        task_id = manager.register(metadata=task_metadata(ctx, {"job_type": "request-test"}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            assert handle.execution.commit(handle.execution.ref.run_id, lambda: committed.append("write")).accepted

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    handle = manager.get_handle(result.data["task_id"])
    metadata = dict(manager.runtime.get(handle.execution.ref, handle.execution.owner).specification.metadata)
    assert metadata["assistant_request_id"] == "request"
    assert "assistant_execution_ref" in metadata and "_assistant_gate" not in metadata
    service.scheduler.deactivate(context.session_id, "view")
    start.set()
    handle._thread.join(5)
    assert not handle._thread.is_alive()
    assert committed == ["write"]
    assert gate.current_request().status == RequestStatus.OPEN
    assert not service.request_task_events.diagnostics


def test_cancel_before_worker_commit_prevents_mutation_and_converges(setup):
    service, context, gate, ctx, manager = setup
    start = Event()
    writes = []
    decisions = []

    def tool(args, ctx):
        task_id = manager.register(metadata=task_metadata(ctx, {}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            decisions.append(handle.execution.commit(handle.execution.ref.run_id, lambda: writes.append("bad")))

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    service.command(context, "request", "cancel", 1)
    service.notify(context.session_id)
    start.set()
    manager.get_handle(result.data["task_id"])._thread.join(5)
    assert decisions and not decisions[0].accepted and not writes
    assert gate.current_request().status == RequestStatus.CANCELLED


@pytest.mark.parametrize("save_failure", [False, True])
def test_plan_leaf_writes_have_distinct_effects_and_complete_only_at_parent_end(setup, monkeypatch, save_failure):
    service, context, gate, ctx, manager = setup
    child_gate = gate.for_dispatch()
    child_context = replace(ctx, assistant_gate=child_gate)
    parent_id = manager.register(metadata=task_metadata(child_context, {"job_type": "assistant-plan"}))
    writes = []
    spec = register(lambda args, ctx: (ctx.safe_mutate_wait(lambda: writes.append("write")), ToolResult.ok("done"))[1])
    states = []
    original = service.update_request

    def save(*args, **kwargs):
        if save_failure and getattr(kwargs.get("cause"), "operation", "") == "task.result":
            raise OSError("parent receipt unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "update_request", save)

    def plan():
        for _ in range(2):
            assert execute_with_guardrails(spec, {}, child_context, middlewares=[]).success
            states.append(gate.current_request().status)

    manager.start_thread(parent_id, plan).join(5)
    if save_failure:
        from concurrent.futures import ThreadPoolExecutor

        assert service.request_task_events.has_pending(context.session_id, "request")
        assert gate.current_request().status == RequestStatus.OPEN
        save_failure = False
        with ThreadPoolExecutor(2) as pool:
            tuple(pool.map(service.request_task_events.retry_pending, [context.session_id] * 2))
        assert not service.request_task_events.has_pending(context.session_id, "request")
    request = gate.current_request()
    assert writes == ["write", "write"] and states == [RequestStatus.OPEN, RequestStatus.OPEN]
    assert len({effect.effect_id for effect in request.effects}) == 2
    assert len({effect.execution.dispatch_id for effect in request.effects}) == 1
    assert request.status == RequestStatus.COMPLETED


def test_input_selection_version_change_is_rejected_before_tool(setup):
    service, context, gate, ctx, _ = setup
    service.transact(
        context,
        lambda state: state.update(
            ingress=[
                {
                    "message_id": "input",
                    "selection": {"active_version_identity": ["old-project", "old-variant"], "variant_revision": 2},
                }
            ]
        ),
    )
    service.update_request(context, "request", lambda request: replace(request, source_message_ids=("input",)))
    writes = []
    spec = register(lambda args, ctx: writes.append("bad"))
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "REQUEST_SCOPE_MISMATCH"
    assert not writes


@pytest.mark.parametrize("with_receipt,partial", [(False, False), (True, True)])
def test_worker_completed_without_proof_or_with_partial_result_does_not_complete_request(setup, with_receipt, partial):
    service, context, gate, ctx, manager = setup

    def tool(args, ctx):
        task_id = manager.register(metadata=task_metadata(ctx, {}))
        handle = manager.get_handle(task_id)

        def work():
            if with_receipt:
                assert handle.execution.commit(handle.execution.ref.run_id, lambda: None).accepted
            if partial:
                manager.update_progress(task_id, {"failed_count": 1, "success_count": 2})

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    manager.get_handle(result.data["task_id"])._thread.join(5)
    request = gate.current_request()
    assert request.status == RequestStatus.OPEN
    assert request.effects[0].status == ("succeeded" if with_receipt else "outcome_unknown")
    assert not request.effects[0].result_complete or not with_receipt


def test_persist_failure_before_effect_prevents_tool_entry(setup, monkeypatch):
    from transbridge.application.assistant_requests.models import RequestError

    service, context, gate, ctx, _ = setup
    writes = []
    spec = register(lambda args, ctx: writes.append("bad"))

    def fail(*args, **kwargs):
        raise RequestError("ADMISSION_PERSIST_FAILED", "disk unavailable")

    monkeypatch.setattr(service, "update_request", fail)
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "ADMISSION_PERSIST_FAILED"
    assert not writes


def test_read_then_write_react_batch_completes_only_after_real_write(setup):
    service, context, gate, ctx, _ = setup
    read = ToolSpec("request_test_read", "read", "read", {}, execute=lambda args, ctx: ToolResult.ok("help"))
    ToolRegistry.register(read)
    writes = []
    write = register(lambda args, ctx: (writes.append("write"), ToolResult.ok("written"))[1])
    gate.prepare_steps([{"tool": read.name, "args": {}}, {"tool": write.name, "args": {}}])
    assert execute_with_guardrails(read, {}, ctx, middlewares=[]).success
    assert gate.current_request().status == RequestStatus.OPEN
    assert not gate.current_request().effects
    assert execute_with_guardrails(write, {}, ctx, middlewares=[]).success
    assert gate.current_request().status == RequestStatus.COMPLETED
    assert writes == ["write"]


def test_read_only_react_batch_never_satisfies_execution_goal(setup):
    service, context, gate, ctx, _ = setup
    read = ToolSpec("request_test_read", "read", "read", {}, execute=lambda args, ctx: ToolResult.ok("help"))
    ToolRegistry.register(read)
    gate.prepare_steps([{"tool": read.name, "args": {}}])
    assert execute_with_guardrails(read, {}, ctx, middlewares=[]).success
    request = gate.current_request()
    assert request.status == RequestStatus.OPEN and not request.unsettled
    assert not request.effects


def test_confirmation_staged_steps_are_not_running_and_read_exception_settles_started_batch(setup):
    service, context, gate, ctx, _ = setup

    def fail(args, ctx):
        raise RuntimeError("read failed")

    read = ToolSpec("request_test_read", "read", "read", {}, execute=fail)
    ToolRegistry.register(read)
    gate.prepare_steps([{"tool": read.name, "args": {}}])
    assert not gate.current_request().dispatches
    with pytest.raises(RuntimeError, match="read failed"):
        execute_with_guardrails(read, {}, ctx, middlewares=[])
    request = gate.current_request()
    assert not request.unsettled and request.status == RequestStatus.OPEN


def test_request_owned_commits_advance_scope_version_for_next_leaf_but_external_change_rejects(setup):
    from types import SimpleNamespace

    service, context, gate, ctx, _ = setup
    app = SimpleNamespace(
        collection=[], active_version_identity=("project", "variant"), project_revision=1, variant_revision=1
    )
    ctx.app_context = app
    ctx._target_version_identity = app.active_version_identity
    ctx._target_project_revision = 1
    ctx._target_variant_revision = 1

    def select_version(state):
        state["ingress"][0]["selection"] = {
            "active_version_identity": ["project", "variant"],
            "project_revision": 1,
            "variant_revision": 1,
        }

    service.transact(context, select_version)
    observed = []

    def write(args, ctx):
        observed.append(ctx._target_variant_revision)

        def mutation():
            app.variant_revision += 1
            ctx._target_variant_revision = app.variant_revision

        assert ctx.assistant_gate.commit(ctx.assistant_effect_id, mutation).accepted
        return ToolResult.ok("written")

    spec = register(write)
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).success
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).success
    assert observed == [1, 2]
    app.variant_revision += 1
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).error_code == "REQUEST_SCOPE_MISMATCH"


def test_task_center_pause_records_wait_and_stopping_request_cannot_resume(setup):
    from transbridge.application.assistant_requests.task_controls import request_task_control
    from transbridge.application.tasks.projection import RuntimeTaskProjection

    service, context, gate, ctx, manager = setup
    start = Event()

    def tool(args, ctx):
        task_id = manager.register(metadata=task_metadata(ctx, {}))
        manager.start_thread(task_id, lambda: start.wait(3))
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    handle = manager.get_handle(result.data["task_id"])
    projection = RuntimeTaskProjection(manager.runtime)
    projection.set_request_control(request_task_control(service, manager.runtime))
    assert projection.control(handle.execution.ref, handle.execution.owner, "pause").accepted
    assert "user_job_control" in gate.current_request().items[0].waiting_reasons
    service.command(context, "request", "cancel", 1)
    assert not projection.control(handle.execution.ref, handle.execution.owner, "resume").accepted
    start.set()
    handle._thread.join(5)
