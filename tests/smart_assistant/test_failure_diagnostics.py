"""Background explanations survive task cleanup without becoming success evidence."""

from dataclasses import replace
from threading import Event

import pytest

from tests.smart_assistant.test_request_execution import register
from transbridge.application.assistant_requests.models import RequestError, UserRequest
from transbridge.application.assistant_requests.result_review import read_request_review
from transbridge.application.assistant_requests.task_diagnostics import append_task_diagnostic
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.task_runtime_bridge import task_metadata
from transbridge.smart_assistant.tools.types import ToolResult

pytest_plugins = ["tests.smart_assistant.test_request_execution"]


def _failed_task(setup, *, committed=False, explicit_terminal=False):
    _, _, _, ctx, manager = setup
    start = Event()
    writes = []

    def tool(args, execution_context):
        task_id = manager.register(metadata=task_metadata(execution_context, {"display_name": "翻译词条"}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            if committed:
                assert handle.execution.commit(handle.execution.ref.run_id, lambda: writes.append("once")).accepted
            if explicit_terminal:
                manager.set_status(task_id, "failed")
                # Existing adapters can send a diagnostic after terminal state.
                manager.update_progress(task_id, {"error": "provider quota exhausted"})
                manager.notify_failed(task_id, "provider quota exhausted")
            else:
                raise RuntimeError("provider quota exhausted")

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    task_id = result.data["task_id"]
    start.set()
    manager.get_handle(task_id)._thread.join(5)
    assert not manager.get_handle(task_id)._thread.is_alive()
    return task_id, writes


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("explicit_terminal", [False, True])
def test_failed_background_reason_survives_reload_without_fabricating_commit(
    setup, monkeypatch, committed, explicit_terminal
):
    service, context, gate, _, manager = setup
    task_id, writes = _failed_task(setup, committed=committed, explicit_terminal=explicit_terminal)
    request = gate.current_request()
    assert request.effects[0].status == "failed"
    assert bool(request.effects[0].receipt) is committed
    evidence = [item for item in request.evidence if item.kind == "task_diagnostic"]
    assert len(evidence) == 1
    assert not evidence[0].complete
    assert not any(item.kind == "execution" for item in request.evidence)
    assert writes == (["once"] if committed else [])
    # Reload exactly the persisted shape and remove access to all transient task state.
    assert UserRequest.from_dict(request.to_dict()).evidence == request.evidence
    with monkeypatch.context() as unavailable:
        unavailable.setattr(manager, "get_status", lambda _: pytest.fail("review read transient task history"))
        unavailable.setattr(manager.runtime, "get", lambda *a: pytest.fail("review read transient runtime"))
        review = read_request_review(service, context, request.request_id)
    assert any(row.status == "failed" and row.message == "provider quota exhausted" for row in review.failures)
    assert not review.changes and not review.undo_available
    assert task_id


def test_diagnostic_persistence_retry_never_replays_work(setup, monkeypatch):
    service, context, gate, _, manager = setup
    update = service.update_request
    recovered = False

    def write(*args, **kwargs):
        if getattr(kwargs.get("cause"), "operation", "") == "task.diagnostic" and not recovered:
            raise OSError("diagnostic storage unavailable")
        return update(*args, **kwargs)

    monkeypatch.setattr(service, "update_request", write)
    _, writes = _failed_task(setup, committed=True, explicit_terminal=True)
    router = service.request_task_events
    assert router.has_pending(context.session_id, gate.admission.request_id)
    assert gate.current_request().effects[0].status == "failed"
    recovered = True
    with monkeypatch.context() as unavailable:
        unavailable.setattr(manager.runtime, "get", lambda *a: pytest.fail("retry must use captured snapshot"))
        service.notify(context.session_id)
    assert not router.has_pending(context.session_id, gate.admission.request_id)
    assert writes == ["once"]
    review = read_request_review(service, context, gate.admission.request_id)
    assert any(row.message == "provider quota exhausted" for row in review.failures)


def test_diagnostic_cannot_attach_to_foreign_job_and_late_diagnostic_does_not_change_state(setup):
    service, context, gate, _, manager = setup
    task_id, _ = _failed_task(setup, explicit_terminal=True)
    handle = manager.get_handle(task_id)
    snapshot = manager.runtime.get(handle.execution.ref, handle.execution.owner)
    request = gate.current_request()
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        append_task_diagnostic(request, replace(snapshot, ref=replace(snapshot.ref, run_id="foreign")), "", "wrong")
    with pytest.raises(ValueError, match="ownership"):
        service.request_task_events.record_diagnostic(
            gate,
            request.effects[0].effect_id,
            replace(snapshot, owner=replace(snapshot.owner, session_id="other")),
            "bad",
        )
    service.request_task_events.record_diagnostic(gate, request.effects[0].effect_id, snapshot, "api_key=secret-value")
    after = gate.current_request()
    assert after.effects == request.effects
    assert after.items == request.items and after.status == request.status
    assert "secret-value" not in after.evidence[-1].reference
    review = read_request_review(service, context, after.request_id)
    assert "secret-value" not in " ".join(row.message for row in review.outcomes)


def test_diagnostic_never_substitutes_for_missing_commit_proof(setup):
    service, context, gate, ctx, manager = setup
    start = Event()

    def tool(args, execution_context):
        task_id = manager.register(metadata=task_metadata(execution_context, {}))
        handle = manager.get_handle(task_id)

        def work():
            assert start.wait(3)
            snapshot = manager.runtime.get(handle.execution.ref, handle.execution.owner)
            service.request_task_events.record_diagnostic(
                gate, handle.execution.assistant_effect_id, snapshot, "无法确认远端是否已提交，请核对结果"
            )

        manager.start_thread(task_id, work)
        return ToolResult.ok("queued", {"task_id": task_id})

    result = execute_with_guardrails(register(tool, long=True), {}, ctx, middlewares=[])
    start.set()
    manager.get_handle(result.data["task_id"])._thread.join(5)
    request = gate.current_request()
    assert request.effects[0].status == "outcome_unknown"
    assert request.effects[0].receipt == ""
    assert request.items[0].status == "waiting"
    assert not request.terminal
    assert all(item.kind == "task_diagnostic" and not item.complete for item in request.evidence)
    review = read_request_review(service, context, request.request_id)
    assert any(row.status == "outcome_unknown" and "远端" in row.message for row in review.failures)
