"""State-machine regressions use concrete requests, attempts and persisted round trips."""

from dataclasses import replace
import json

import pytest

from transbridge.application.assistant_requests.admission import (
    activate_effect,
    bind_job,
    prepare_effect,
    record_effect_outcome,
    validate_effect,
)
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectStatus,
    ItemKind,
    ItemStatus,
    RequestError,
    RequestItem,
    RequestStatus,
    UserRequest,
)
from transbridge.application.assistant_requests.recovery import recover_request
from transbridge.application.assistant_requests.reducer import RequestEvent, reduce_request
from transbridge.application.assistant_requests.scheduler import RequestScheduler, commit_answer, ready_item_ids


def make_request(*, execution=False):
    return UserRequest(
        "r1",
        "s1",
        "完成工作",
        tuple(
            RequestItem(f"i{n}", f"事项{n}", kind=ItemKind.EXECUTION if execution else ItemKind.ANSWER)
            for n in range(3)
        ),
    )


def admit(request):
    scheduler = RequestScheduler()
    scheduler.activate(request.session_id, "view")
    selected = scheduler.select_next_turn(request.session_id, "view", (request,), allowed_tools=("write",))
    return scheduler, selected.request, selected.admission


def prepare(request, scheduler, turn, effect_id="e1", item_id="i0"):
    execution = AssistantExecutionRef(request.request_id, request.revision, (item_id,), "a1", "d1", turn.turn_id, "s1")
    return prepare_effect(
        request,
        turn,
        execution,
        effect_id=effect_id,
        operation={"write": item_id},
        tool_name="write",
        scheduler=scheduler,
    )


def test_answer_only_completes_covered_items_and_round_trip():
    scheduler, request, turn = admit(make_request())
    updated = commit_answer(
        request,
        turn,
        "前两项说明",
        {"i0": "answered", "i1": "answered"},
        message_id="m1",
        finish_reason="stop",
        scheduler=scheduler,
    )
    assert updated.status == RequestStatus.OPEN
    assert ready_item_ids(updated) == ("i2",)
    restored = UserRequest.from_dict(json.loads(json.dumps(updated.to_dict())))
    assert restored == updated
    completed = commit_answer(
        restored, turn, "最后一项说明", {"i2": "answered"}, message_id="m2", finish_reason="stop", scheduler=scheduler
    )
    assert completed.status == RequestStatus.COMPLETED


@pytest.mark.parametrize(
    "text,reason,coverage",
    [
        ("", "stop", {"i0": "answered"}),
        ("文字", "length", {"i0": "answered"}),
        ("文字", "stop", {}),
        ("文字", "error", {"i0": "answered"}),
    ],
)
def test_incomplete_answer_does_not_complete(text, reason, coverage):
    scheduler, request, turn = admit(make_request())
    with pytest.raises(RequestError, match="ANSWER_INCOMPLETE"):
        commit_answer(request, turn, text, coverage, message_id="m1", finish_reason=reason, scheduler=scheduler)
    assert all(i.status == ItemStatus.PENDING for i in request.items)


def test_answer_cannot_claim_execution_success():
    scheduler, request, turn = admit(make_request(execution=True))
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        commit_answer(
            request, turn, "已写入", {"i0": "answered"}, message_id="m", finish_reason="stop", scheduler=scheduler
        )


def test_pause_blocks_new_admission_without_revoking_an_already_running_effect():
    scheduler, request, turn = admit(make_request(execution=True))
    running = activate_effect(prepare(request, scheduler, turn), "e1")
    paused = reduce_request(running, RequestEvent("pause", "pause", 1))
    assert validate_effect(paused, "e1").status == EffectStatus.RUNNING
    with pytest.raises(RequestError, match="REQUEST_PAUSED"):
        prepare(paused, scheduler, turn, "another")
    stopped = reduce_request(paused, RequestEvent("cancel", "cancel", 1))
    with pytest.raises(RequestError):
        validate_effect(stopped, "e1")


def test_stop_waits_for_unknown_effect_and_rejects_resume():
    scheduler, request, turn = admit(make_request(execution=True))
    request = activate_effect(prepare(request, scheduler, turn), "e1")
    request = reduce_request(request, RequestEvent("cancel", "cancel", 1))
    assert request.status == RequestStatus.STOPPING
    with pytest.raises(RequestError, match="REQUEST_TERMINAL"):
        reduce_request(request, RequestEvent("resume", "resume", 1))
    request = recover_request(request)
    assert request.status == RequestStatus.STOPPING
    assert request.effects[0].status == EffectStatus.OUTCOME_UNKNOWN
    request = record_effect_outcome(request, "e1", status="succeeded", receipt="receipt", sequence=2)
    assert request.status == RequestStatus.CANCELLED
    assert request.evidence[0].reference == "receipt"
    assert record_effect_outcome(request, "e1", status="succeeded", receipt="receipt", sequence=3) == request


def test_old_revision_results_are_audit_only_and_lease_revoked():
    scheduler, request, turn = admit(make_request(execution=True))
    request = prepare(request, scheduler, turn)
    request = reduce_request(request, RequestEvent("amend", "amend", 1, {"source_message_id": "new", "goal": "新目标"}))
    with pytest.raises(RequestError, match="REQUEST_REVISION_CONFLICT"):
        validate_effect(request, "e1")
    request = record_effect_outcome(request, "e1", status="succeeded", receipt="old receipt")
    assert all(i.status == ItemStatus.PENDING for i in request.items)
    assert request.evidence[0].request_revision == 1
    assert request.revision == 2
    assert UserRequest.from_dict(json.loads(json.dumps(request.to_dict()))) == request


def test_failed_item_does_not_end_request_with_other_live_work():
    scheduler, request, turn = admit(make_request(execution=True))
    request = prepare(request, scheduler, turn)
    scheduler.release(turn)
    selected = scheduler.select_next_turn("s1", "view", (request,), allowed_tools=("write",))
    turn = selected.admission
    request = selected.request
    request = prepare(request, scheduler, turn, "e2", "i1")
    request = record_effect_outcome(request, "e1", status="failed")
    request = reduce_request(request, RequestEvent("fail2", "fail", 1, {"item_ids": ["i2"]}))
    assert request.status == RequestStatus.OPEN
    request = record_effect_outcome(request, "e2", status="succeeded", receipt="actual commit")
    assert request.status == RequestStatus.FAILED
    assert request.items[1].status == ItemStatus.SATISFIED


def test_partial_task_outcome_requires_further_item_evidence():
    scheduler, request, turn = admit(make_request(execution=True))
    request = prepare(request, scheduler, turn)
    request = record_effect_outcome(request, "e1", status="succeeded", receipt="partial result", item_complete=False)
    assert request.items[0].status == ItemStatus.PENDING
    assert request.status == RequestStatus.OPEN


def test_effect_id_deduplicates_but_wrong_job_result_rejected():
    scheduler, request, turn = admit(make_request(execution=True))
    request = prepare(request, scheduler, turn)
    assert prepare(request, scheduler, turn) == request
    request = bind_job(request, "e1", job_id="job", run_id="run")
    with pytest.raises(RequestError, match="REQUEST_SCOPE_MISMATCH"):
        record_effect_outcome(request, "e1", status="succeeded", receipt="receipt", job_id="other", run_id="run")


def test_pause_resume_retains_item_waits_and_command_deduplication():
    request = reduce_request(
        make_request(), RequestEvent("wait", "wait", 1, {"item_ids": ["i0"], "reason": "confirmation"})
    )
    event = RequestEvent("pause", "pause", 1)
    paused = reduce_request(request, event)
    assert reduce_request(paused, event) == paused
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        reduce_request(paused, replace(event, kind="cancel"))
    resumed = reduce_request(paused, RequestEvent("resume", "resume", 1))
    assert ready_item_ids(resumed) == ("i1", "i2")
    assert resumed.revision == 1


def test_dependencies_are_acyclic_and_waiting_blocks_only_dependants():
    with pytest.raises(RequestError, match="acyclic"):
        UserRequest(
            "r", "s", "goal", (RequestItem("a", "a", dependencies=("b",)), RequestItem("b", "b", dependencies=("a",)))
        )
    request = UserRequest(
        "r",
        "s",
        "goal",
        (
            RequestItem("a", "a", waiting_reasons=("confirmation",)),
            RequestItem("b", "b", dependencies=("a",)),
            RequestItem("c", "c"),
        ),
    )
    assert ready_item_ids(request) == ("c",)
