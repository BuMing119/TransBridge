"""Control-call terminal receipts survive delivery races and Session reloads."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from uuid import uuid4

import pytest

from tests.application.assistant_context.test_history_queries import _request
from tests.application.assistant_requests.test_request_repository import _snapshot
from transbridge.application.assistant_requests.control_operations import recover_control_calls
from transbridge.application.assistant_requests.control_results import commit_control_result, finish_control_calls
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.scheduler import commit_answer
from transbridge.bootstrap.persistence import build_persistence_v2_services

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]
TOOL = "read_request_state"


def _setup(composed, *, name=TOOL, persist=True):
    _, service, context = composed
    identity = _request(service, context)
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.select_next_turn(
        context.session_id, "view", service.requests(service.state(context)), allowed_tools=(name,)
    ).admission
    parent = {
        "role": "assistant",
        "content": "回答",
        "message_id": "parent",
        "control_turn_id": admission.turn_id,
        "tool_calls": [{"id": "call", "name": name, "arguments": {}}],
    }
    if persist:
        service.save_history(context, [parent], request_id=identity)
    return service, context, admission


def _results(composed):
    services, _, context = composed
    return [m for m in _snapshot(services, context).backend_messages() if m["role"] == "tool"]


def test_commit_is_durable_owned_and_idempotent_after_lease_release(composed):
    service, context, admission = _setup(composed)
    first = commit_control_result(service, context, admission, "call", TOOL, {"text": "一页"})
    service.scheduler.release(admission)
    again = commit_control_result(service, context, admission, "call", TOOL, {"text": "一页"})
    assert first == again == _results(composed)[0]
    assert len(_results(composed)) == 1
    assert service.state(context)["result_owners"][first["message_id"]] == admission.request_id
    with pytest.raises(RequestError, match="CONTROL_RESULT_CONFLICT"):
        commit_control_result(service, context, admission, "call", TOOL, {"text": "不同"})


def test_missing_parent_and_foreign_request_rejected(composed):
    service, context, admission = _setup(composed, persist=False)
    with pytest.raises(RequestError, match="CONTROL_PARENT_MISSING"):
        commit_control_result(service, context, admission, "call", TOOL, {})
    foreign = _request(service, context, "other", "other")
    service.save_history(
        context,
        [
            {
                "role": "assistant",
                "content": "",
                "message_id": "foreign",
                "tool_calls": [{"id": "call", "name": TOOL, "arguments": {}}],
            }
        ],
        request_id=foreign,
    )
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        commit_control_result(service, context, admission, "call", TOOL, {})
    assert not _results(composed)


def test_revoked_admission_cannot_start_query_or_commit(composed):
    service, context, admission = _setup(composed)
    service.scheduler.release(admission)

    def forbidden():
        pytest.fail("revoked query executed")

    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        commit_control_result(service, context, admission, "call", TOOL, forbidden)
    assert not _results(composed)


def test_cancel_before_success_finishes_exactly_once(composed):
    service, context, admission = _setup(composed)
    cancelled = finish_control_calls(service, context, admission, [("call", TOOL)])
    assert cancelled[0]["is_error"]
    assert finish_control_calls(service, context, admission, [("call", TOOL)]) == cancelled
    with pytest.raises(RequestError, match="CONTROL_RESULT_CONFLICT"):
        commit_control_result(service, context, admission, "call", TOOL, {"text": "late"})
    assert _results(composed) == cancelled


def test_success_before_cancel_returns_original_receipt_without_second_result(composed):
    service, context, admission = _setup(composed)
    success = commit_control_result(service, context, admission, "call", TOOL, {"text": "done"})
    assert finish_control_calls(service, context, admission, [("call", TOOL)]) == [success]
    assert _results(composed) == [success]


def test_query_retry_returns_recorded_page_without_rereading(composed):
    service, context, admission = _setup(composed)
    first = commit_control_result(service, context, admission, "call", TOOL, lambda: {"text": "old"})
    assert commit_control_result(service, context, admission, "call", TOOL, lambda: pytest.fail("reread")) == first


def test_answer_and_receipt_commit_together_and_retry_does_not_duplicate_evidence(composed):
    name = "report_answer_coverage"
    service, context, admission = _setup(composed, name=name)

    def answer(request, parent):
        return commit_answer(
            request,
            admission,
            parent["content"],
            {"answer": "answered"},
            message_id=parent["message_id"],
            finish_reason="tool_calls",
            scheduler=service.scheduler,
        )

    receipt = commit_control_result(service, context, admission, "call", name, {"accepted": True}, change=answer)
    assert (
        commit_control_result(service, context, admission, "call", name, {"accepted": True}, change=answer) == receipt
    )
    request = service.requests(service.state(context))[0]
    assert len(request.evidence) == 1 and request.evidence[0].reference == "parent"
    assert _results(composed) == [receipt]


def test_failed_change_leaves_no_receipt_or_request_mutation(composed):
    service, context, admission = _setup(composed)
    before = service.state(context)

    def reject(request, parent):
        raise RequestError("DESIGN_REJECTED", "explicit failure")

    with pytest.raises(RequestError, match="DESIGN_REJECTED"):
        commit_control_result(service, context, admission, "call", TOOL, {}, change=reject)
    assert service.state(context) == before
    assert not _results(composed)


def test_scheduler_handoff_cannot_split_validation_and_commit(composed):
    service, context, admission = _setup(composed)
    entered, finish, handoff_started = Event(), Event(), Event()

    def read():
        entered.set()
        assert finish.wait(5)
        return {"text": "committed before handoff"}

    def handoff():
        handoff_started.set()
        service.scheduler.deactivate(context.session_id, "view")

    with ThreadPoolExecutor(max_workers=2) as workers:
        committed = workers.submit(commit_control_result, service, context, admission, "call", TOOL, read)
        assert entered.wait(5)
        revoked = workers.submit(handoff)
        try:
            assert handoff_started.wait(5)
            assert not revoked.done()
        finally:
            finish.set()
        receipt = committed.result(timeout=5)
        revoked.result(timeout=5)
    assert json.loads(receipt["content"])["text"] == "committed before handoff"
    assert _results(composed) == [receipt]


def test_foreign_scope_cannot_cancel_owned_call(composed):
    service, context, admission = _setup(composed)
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        finish_control_calls(service, context, replace(admission, scope=(("owner", "other"),)), [("call", TOOL)])
    assert not _results(composed)


def test_receipt_staging_failure_does_not_publish_answer_evidence(composed, monkeypatch):
    name = "report_answer_coverage"
    service, context, admission = _setup(composed, name=name)
    before = service.state(context)

    def fail_storage(*args, **kwargs):
        raise RequestError("STORAGE_FAILED", "cannot stage receipt")

    def answer(request, parent):
        return commit_answer(
            request,
            admission,
            parent["content"],
            {"answer": "answered"},
            message_id=parent["message_id"],
            finish_reason="tool_calls",
            scheduler=service.scheduler,
        )

    monkeypatch.setattr(service, "with_transcript", fail_storage)
    with pytest.raises(RequestError, match="STORAGE_FAILED"):
        commit_control_result(service, context, admission, "call", name, {"accepted": True}, change=answer)
    assert service.state(context) == before
    assert not _results(composed)


def test_reopen_returns_committed_receipt_without_reacquiring_old_lease(composed):
    services, _, _ = composed
    service, context, admission = _setup(composed)
    receipt = commit_control_result(service, context, admission, "call", TOOL, {"text": "saved"})
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests
        assert finish_control_calls(restored, context, admission, [("call", TOOL)]) == [receipt]
    finally:
        reopened.close()


def test_new_lease_cannot_finish_old_pending_control_as_success(composed):
    service, context, admission = _setup(composed)
    service.scheduler.release(admission)
    newer = service.scheduler.select_next_turn(
        context.session_id, "view", service.requests(service.state(context)), allowed_tools=(TOOL,)
    ).admission
    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        commit_control_result(service, context, newer, "call", TOOL, {})
    cancelled = finish_control_calls(service, context, admission, [("call", TOOL)])
    assert cancelled[0]["is_error"]
    service.scheduler.validate(newer)


def test_recovery_closes_owned_pending_parent_once(composed):
    service, context, admission = _setup(composed)
    service.scheduler.release(admission)
    receipts = recover_control_calls(service, context)
    assert len(receipts) == 1 and receipts[0]["is_error"]
    assert recover_control_calls(service, context) == []
    assert _results(composed) == receipts
