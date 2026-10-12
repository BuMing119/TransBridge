"""Cross-workflow regressions for request admission and ReAct retry/batch completion."""

from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import ItemKind, RequestError, RequestItem, UserRequest
from transbridge.application.assistant_requests.recovery import recover_request
from transbridge.application.assistant_requests.scheduler import ready_item_ids
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.reflexion.retry_executor import ToolRetryExecutor
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.types import ToolResult


@pytest.fixture
def admitted_handler(tmp_path, monkeypatch):
    TaskManager.reset()
    services = build_persistence_v2_services(
        tmp_path / "v2", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-07T00:00:00Z"
    )
    assert services.gui_session_commands.create_and_activate("audit", RequestContext("owner")).is_success
    session_id = services.session_lifecycle.active.aggregate.ref.identity.value
    context = RequestContext("owner", session_id=session_id)
    service = services.gui_session_commands.assistant_requests
    source = service.accept_input(context, "inspect and translate", selection={})
    request = UserRequest(
        "audit-request",
        session_id,
        "inspect and translate",
        (RequestItem("work", "translate", ItemKind.EXECUTION),),
        source_message_ids=(source["message_id"],),
        work_round_id=source["message_id"],
    )

    def save_request(state):
        state["requests"] = [request.to_dict()]
        state["ingress"][0]["status"] = "applied"

    service.transact(context, save_request)
    service.scheduler.activate(session_id, "audit-view")
    selection = service.scheduler.select_next_turn(
        session_id, "audit-view", (request,), allowed_tools=("audit_read", "audit_write")
    )
    gate = RequestExecutionGate(service, context, selection.admission)
    handler = ToolExecutionHandler(
        SimpleNamespace(request_context=context), SimpleNamespace(add_observation=lambda *args: None)
    )
    handler.assistant_gate_provider = lambda: gate
    handler._middlewares = [InputValidationGuard()]
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", {"default": {}})
    monkeypatch.setattr(
        ToolRetryExecutor, "_wait_before_retry", staticmethod(lambda delay, cancelled: not (cancelled and cancelled()))
    )
    try:
        yield gate, handler
    finally:
        TaskManager.reset()
        services.close()


@pytest.mark.parametrize("first_failure", ["transient", "invalid_arguments"])
def test_admitted_read_retries_can_complete_the_original_step(admitted_handler, first_failure):
    gate, handler = admitted_handler
    calls = []

    def read(args, context):
        calls.append(args)
        if first_failure == "transient" and len(calls) == 1:
            return ToolResult.fail("temporary failure", error_category="network", error_code="TIMEOUT")
        return ToolResult.ok("read completed")

    spec = ToolSpec("audit_read", "read", "read", {"query": {"type": "str", "required": True}}, execute=read)
    ToolRegistry.register(spec)
    client = SimpleNamespace(chat=lambda *args, **kwargs: json.dumps({"retry": True, "adjusted_args": {"query": "ok"}}))
    handler.retry_handler._llm = client
    steps = [{"tool": spec.name, "args": {"query": "ok" if first_failure == "transient" else 123}}]
    gate.prepare_steps(steps)

    completed = handler.execute_steps_detached(steps)

    assert completed[0].result.success, completed[0].result.to_dict()
    assert len(calls) == (2 if first_failure == "transient" else 1)
    assert gate.current_request().dispatches[0].status == "completed"
    assert not gate.current_request().unsettled


def test_rejected_later_read_does_not_leave_request_batch_running(admitted_handler):
    gate, handler = admitted_handler
    calls = []
    spec = ToolSpec(
        "audit_read",
        "read",
        "read",
        {"query": {"type": "str", "required": True}},
        execute=lambda args, context: (calls.append(args), ToolResult.ok("read completed"))[1],
    )
    ToolRegistry.register(spec)
    steps = [{"tool": spec.name, "args": {"query": "ok"}}, {"tool": spec.name, "args": {"query": 123}}]
    gate.prepare_steps(steps)

    completed = handler.execute_steps_detached(steps)

    assert completed[0].result.success
    assert completed[1].result.error_code == "ARGUMENT_SCHEMA_INVALID"
    assert calls == [{"query": "ok"}]
    request = gate.current_request()
    assert not request.unsettled, request.dispatches
    assert ready_item_ids(request) == ("work",)


def register_read(execute, *, parameters=None):
    spec = ToolSpec("audit_read", "read", "read", parameters or {}, execute=execute)
    ToolRegistry.register(spec)
    return spec


def test_identical_steps_keep_distinct_identity_across_retries(admitted_handler):
    gate, handler = admitted_handler
    calls = []

    def read(args, context):
        calls.append(args)
        return ToolResult.ok() if len(calls) % 2 == 0 else ToolResult.fail("retry", error_category="network")

    register_read(read)
    steps = [{"tool": "audit_read", "args": {}}] * 2
    gate.prepare_steps(steps)
    assert all(item.result.success for item in handler.execute_steps_detached(steps))
    dispatch = gate.current_request().dispatches[0]
    assert dispatch.completed_steps == ("0", "1")
    assert [(a.step_id, a.attempt, a.status) for a in dispatch.step_attempts] == [
        ("0", 1, "failed"),
        ("0", 2, "completed"),
        ("1", 1, "failed"),
        ("1", 2, "completed"),
    ]
    assert len(dispatch.step_receipts) == 2


@pytest.mark.parametrize("queries", [[123, "ok"], [123, 456], ["ok", 123]])
def test_guard_rejection_anywhere_closes_the_batch(admitted_handler, queries):
    gate, handler = admitted_handler
    register_read(lambda args, context: ToolResult.ok(), parameters={"query": {"type": "str", "required": True}})
    steps = [{"tool": "audit_read", "args": {"query": query}} for query in queries]
    gate.prepare_steps(steps)
    results = handler.execute_steps_detached(steps)
    assert [result.result.success for result in results] == [isinstance(query, str) for query in queries]
    assert gate.current_request().dispatches[0].status == "failed"
    assert not gate.current_request().unsettled
    assert ready_item_ids(gate.current_request()) == ("work",)


@pytest.mark.parametrize("unavailable", [False, True])
def test_missing_or_unavailable_admitted_tool_has_a_receipt(admitted_handler, unavailable):
    gate, handler = admitted_handler
    spec = register_read(lambda args, context: pytest.fail("unavailable tool executed"))
    steps = [{"tool": spec.name, "args": {}}]
    gate.prepare_steps(steps)
    if unavailable:
        ToolRegistry._namespaced_tools["default"][spec.name] = replace(spec, available=False)
    else:
        ToolRegistry._namespaced_tools["default"].pop(spec.name)
    result = handler.execute_steps_detached(steps)[0].result
    assert not result.success
    assert gate.current_request().dispatches[0].completed_steps == ("0",)
    assert not gate.current_request().unsettled


@pytest.mark.parametrize("during_retry", [False, True])
def test_cancel_settles_current_and_unstarted_steps(admitted_handler, during_retry):
    gate, handler = admitted_handler
    calls = []
    cancelled = False

    def read(args, context):
        nonlocal cancelled
        calls.append(args)
        if during_retry:
            return ToolResult.fail("retry", error_category="network")
        cancelled = True
        return ToolResult.ok()

    def on_retry(message):
        nonlocal cancelled
        cancelled = True

    register_read(read)
    steps = [{"tool": "audit_read", "args": {}}] * 2
    gate.prepare_steps(steps)
    handler.execute_steps_detached(steps, on_retry, lambda: cancelled)
    dispatch = gate.current_request().dispatches[0]
    assert len(calls) == 1
    assert not gate.current_request().unsettled
    assert dispatch.completed_steps == ("0", "1")
    assert dispatch.step_receipts[-1].status == "cancelled"
    assert dispatch.step_receipts[0].status == ("cancelled" if during_retry else "completed")


@pytest.mark.parametrize("revoke", ["lease", "scope"])
def test_retry_revalidates_turn_and_scope(admitted_handler, revoke):
    gate, handler = admitted_handler
    calls = []
    register_read(lambda args, context: (calls.append(args), ToolResult.fail("retry", error_category="network"))[1])
    steps = [{"tool": "audit_read", "args": {}}]
    gate.prepare_steps(steps)

    def invalidate(message):
        if revoke == "lease":
            gate.service.scheduler.release(gate.admission)
        else:
            gate.service.update_request(
                gate.context, gate.admission.request_id, lambda r: replace(r, scope=(("x", "y"),))
            )

    result = handler.execute_steps_detached(steps, invalidate)[0].result
    assert result.error_code == "TURN_LEASE_STALE"
    assert len(calls) == 1
    assert not gate.current_request().unsettled


def test_lease_revocation_between_steps_closes_remaining_batch(admitted_handler):
    gate, handler = admitted_handler
    calls = []

    def read(args, context):
        calls.append(args)
        gate.service.scheduler.release(gate.admission)
        return ToolResult.ok()

    register_read(read)
    steps = [{"tool": "audit_read", "args": {}}] * 2
    gate.prepare_steps(steps)
    results = handler.execute_steps_detached(steps)
    assert results[0].result.success and results[1].result.error_code == "TURN_LEASE_STALE"
    assert len(calls) == 1
    assert not gate.current_request().unsettled
    assert gate.current_request().dispatches[0].completed_steps == ("0", "1")


def test_model_cannot_inject_step_identity(admitted_handler):
    gate, handler = admitted_handler
    register_read(
        lambda args, context: pytest.fail("forged identity executed"), parameters={"assistant_step_id": {"type": "str"}}
    )
    steps = [{"tool": "audit_read", "args": {"assistant_step_id": "0"}}]
    gate.prepare_steps(steps)
    assert handler.execute_steps_detached(steps)[0].result.error_code == "REQUEST_PROTOCOL_INVALID"
    assert not gate.current_request().unsettled


def test_write_failures_do_not_gain_automatic_retry(admitted_handler):
    gate, handler = admitted_handler
    calls = []
    ToolRegistry.register(
        ToolSpec(
            "audit_write",
            "write",
            "write",
            {},
            permission="write",
            execute=lambda args, context: (calls.append(args), ToolResult.fail("retry", error_category="network"))[1],
        )
    )
    step = {"tool": "audit_write", "args": {}}
    gate.prepare_steps([step])
    assert not handler.execute_step(step).success
    assert len(calls) == 1
    assert len(gate.current_request().dispatches[0].step_attempts) == 1


def test_retry_exhaustion_has_one_logical_terminal_receipt(admitted_handler):
    gate, handler = admitted_handler
    calls = []
    register_read(lambda args, context: (calls.append(args), ToolResult.fail("retry", error_category="network"))[1])
    steps = [{"tool": "audit_read", "args": {}}]
    gate.prepare_steps(steps)
    assert not handler.execute_steps_detached(steps)[0].result.success
    dispatch = gate.current_request().dispatches[0]
    assert len(calls) == 4
    assert len(dispatch.step_attempts) == 4 and len(dispatch.step_receipts) == 1
    assert dispatch.status == "failed" and not gate.current_request().unsettled


def test_read_exception_closes_attempt_and_logical_step(admitted_handler):
    gate, handler = admitted_handler

    def fail(args, context):
        raise ValueError("invalid result")

    register_read(fail)
    steps = [{"tool": "audit_read", "args": {}}]
    gate.prepare_steps(steps)
    assert handler.execute_steps_detached(steps)[0].result.error_code == "ValueError"
    dispatch = gate.current_request().dispatches[0]
    assert dispatch.step_attempts[0].status == "failed"
    assert dispatch.status == "failed" and not gate.current_request().unsettled


@pytest.mark.parametrize("step_count", [1, 2])
def test_step_receipt_failure_blocks_new_work_and_retries_only_receipt(admitted_handler, monkeypatch, step_count):
    gate, handler = admitted_handler
    calls = []
    register_read(lambda args, context: (calls.append(args), ToolResult.ok())[1])
    steps = [{"tool": "audit_read", "args": {}}] * step_count
    gate.prepare_steps(steps)
    update_request = gate.service.update_request

    def fail_receipt(*args, **kwargs):
        if getattr(kwargs.get("cause"), "operation", "") == "step.result":
            raise OSError("receipt disk failure")
        return update_request(*args, **kwargs)

    monkeypatch.setattr(gate.service, "update_request", fail_receipt)
    result = handler.execute_steps_detached(steps)[0].result
    assert result.error_code == "ADMISSION_PERSIST_FAILED"
    assert gate.current_request().unsettled
    with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
        gate.begin_step("audit_read", {})
    monkeypatch.setattr(gate.service, "update_request", update_request)
    gate.service.notify(gate.context.session_id)
    assert len(calls) == 1
    assert not gate.current_request().unsettled
    assert gate.current_request().dispatches[0].status == ("completed" if step_count == 1 else "failed")


def test_claim_save_failure_after_commit_does_not_abandon_started_step(admitted_handler, monkeypatch):
    gate, handler = admitted_handler
    register_read(lambda args, context: pytest.fail("unacknowledged step claim must not execute"))
    steps = [{"tool": "audit_read", "args": {}}] * 2
    gate.prepare_steps(steps)
    update_request = gate.service.update_request
    injected = False

    def fail_after_claim(*args, **kwargs):
        nonlocal injected
        saved = update_request(*args, **kwargs)
        if not injected and saved.dispatches and saved.dispatches[0].started_steps:
            injected = True
            raise OSError("claim committed but delivery failed")
        return saved

    monkeypatch.setattr(gate.service, "update_request", fail_after_claim)
    results = handler.execute_steps_detached(steps)
    assert results[0].result.error_code == "ADMISSION_PERSIST_FAILED"
    assert injected and not gate.current_request().unsettled
    assert gate.current_request().dispatches[0].completed_steps == ("0", "1")


def test_late_duplicate_receipt_does_not_change_original_terminal(admitted_handler):
    gate, handler = admitted_handler
    register_read(lambda args, context: ToolResult.ok())
    gate.prepare_steps([{"tool": "audit_read", "args": {}}])
    handle = gate.begin_step("audit_read", {})
    gate.finish_step(handle, ToolResult.fail("stopped", error_category="cancelled"))
    gate.finish_step(handle, ToolResult.ok())
    dispatch = gate.current_request().dispatches[0]
    assert len(dispatch.step_receipts) == 1
    assert dispatch.step_receipts[0].status == "cancelled"
    assert dispatch.status == "failed"


def test_late_result_cannot_revive_cancelled_request(admitted_handler):
    gate, handler = admitted_handler
    register_read(lambda args, context: ToolResult.ok())
    gate.prepare_steps([{"tool": "audit_read", "args": {}}])
    handle = gate.begin_step("audit_read", {})
    gate.service.command(gate.context, gate.admission.request_id, "cancel", gate.admission.request_revision)
    gate.finish_step(handle, ToolResult.ok())
    assert gate.current_request().status == "cancelled"
    assert gate.current_request().dispatches[0].status == "cancelled"


def test_interrupt_receipts_retry_after_save_failure_without_executing_steps(admitted_handler, monkeypatch):
    gate, handler = admitted_handler
    register_read(lambda args, context: pytest.fail("interrupted step executed"))
    gate.prepare_steps([{"tool": "audit_read", "args": {}}] * 3)
    handle = gate.begin_step("audit_read", {})
    gate.finish_step(handle, ToolResult.ok())
    update_request = gate.service.update_request

    def fail(*args, **kwargs):
        if getattr(kwargs.get("cause"), "operation", "") == "step.result":
            raise OSError("unavailable storage")
        return update_request(*args, **kwargs)

    monkeypatch.setattr(gate.service, "update_request", fail)
    with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
        gate.interrupt_steps()
    monkeypatch.setattr(gate.service, "update_request", update_request)
    gate.service.notify(gate.context.session_id)
    dispatch = gate.current_request().dispatches[0]
    assert dispatch.completed_steps == ("0", "1", "2")
    assert dispatch.failed_steps == ("1", "2")
    assert not gate.current_request().unsettled


def test_interrupted_unstarted_step_cannot_be_claimed_late(admitted_handler):
    gate, handler = admitted_handler
    register_read(lambda args, context: ToolResult.ok())
    gate.prepare_steps([{"tool": "audit_read", "args": {}}] * 2)
    handle = gate.begin_step("audit_read", {})
    gate.interrupt_steps()
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        gate.begin_step("audit_read", {})
    gate.finish_step(handle, ToolResult.ok())
    assert not gate.current_request().unsettled


@pytest.mark.parametrize("legacy", [False, True])
def test_recovery_requires_persisted_no_write_evidence(admitted_handler, legacy):
    gate, handler = admitted_handler
    register_read(lambda args, context: ToolResult.ok())
    gate.prepare_steps([{"tool": "audit_read", "args": {}}] * 2)
    handle = gate.begin_step("audit_read", {})
    gate.finish_step(handle, ToolResult.ok())
    request = UserRequest.from_dict(gate.current_request().to_dict())
    if legacy:
        request = replace(request, dispatches=(replace(request.dispatches[0], step_permissions=()),))
    recovered = recover_request(request)
    assert recovered.dispatches[0].status == ("outcome_unknown" if legacy else "failed")
    assert recovered.unsettled is legacy
    assert recover_request(recovered).dispatches == recovered.dispatches


def test_queued_write_waits_for_actual_job_terminal(admitted_handler):
    gate, handler = admitted_handler
    manager = TaskManager()
    release = Event()

    def queue(args, context):
        task_id = manager.register(metadata=task_metadata(context, {}))
        handle = manager.get_handle(task_id)

        def work():
            assert release.wait(5)
            assert handle.execution.commit(handle.execution.ref.run_id, lambda: None).accepted

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    ToolRegistry.register(
        ToolSpec("audit_write", "write", "write", {}, permission="write", is_long_running=True, execute=queue)
    )
    register_read(lambda args, context: ToolResult.ok())
    steps = [{"tool": "audit_write", "args": {}}, {"tool": "audit_read", "args": {}}]
    gate.prepare_steps(steps)
    result = handler.execute_step(steps[0])
    try:
        assert handler.execute_step(steps[1]).success
        assert gate.current_request().dispatches[0].status == "running"
        assert gate.current_request().unsettled
        effect = gate.current_request().effects[0]
        recovered_live = recover_request(gate.current_request(), live_job_ids=frozenset({effect.job_id}))
        assert recovered_live.dispatches[0].status == "running"
        assert recovered_live.effects[0].status == "running"
        recovered_missing = recover_request(gate.current_request())
        assert recovered_missing.dispatches[0].status == "outcome_unknown"
        assert recovered_missing.effects[0].status == "outcome_unknown"
    finally:
        release.set()
        manager.get_handle(result.data["task_id"])._thread.join(5)
    assert gate.current_request().dispatches[0].status == "completed"
    assert not gate.current_request().unsettled
