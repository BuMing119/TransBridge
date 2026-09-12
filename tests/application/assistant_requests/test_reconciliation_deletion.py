from dataclasses import replace
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.deletion import prepare_session_deletion
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    RequestError,
    RequestItem,
    UserRequest,
)
from transbridge.application.assistant_requests.reconciliation import (
    EffectResolution,
    RequestReconciliationCoordinator,
    reconcile_effect,
)
from transbridge.application.assistant_requests.reducer import RequestEvent, reduce_request
from transbridge.application.assistant_requests.scheduler import RequestScheduler
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services


@pytest.fixture
def saved(tmp_path):
    services = build_persistence_v2_services(tmp_path, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    assert services.gui_session_commands.create_and_activate("A", RequestContext("owner")).is_success
    ref = services.session_lifecycle.active.aggregate.ref
    context = RequestContext("owner", session_id=ref.identity.value)
    yield services, services.gui_session_commands.assistant_requests, context, ref
    services.close()


def unknown(session="session"):
    execution = AssistantExecutionRef("request", 1, ("item",), "original-attempt", "dispatch", "turn", session)
    effect = EffectIntent("effect", "operation-hash", execution, 0, EffectStatus.OUTCOME_UNKNOWN)
    return UserRequest(
        "request", session, "Write", (RequestItem("item", "Write", kind="execution"),), effects=(effect,)
    )


def test_reconciliation_records_original_attempt_without_automatic_replay():
    request = unknown()
    resolution = EffectResolution(
        "confirm",
        "effect",
        1,
        "operation-hash",
        "succeeded",
        "inspected output file",
        "user_attestation",
        "input",
        True,
    )
    updated = reconcile_effect(request, resolution)
    assert updated.effects[0].execution.attempt_id == "original-attempt"
    assert updated.effects[0].status == "succeeded"
    assert any(e.kind == "reconciliation" for e in updated.evidence)
    assert reconcile_effect(updated, resolution) == updated
    with pytest.raises(RequestError, match="COMMAND_PAYLOAD_CONFLICT"):
        reconcile_effect(updated, replace(resolution, reference="different"))


def test_model_text_cannot_resolve_unknown_effect():
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        reconcile_effect(
            unknown(),
            EffectResolution("c", "effect", 1, "operation-hash", "succeeded", "model said so", "model", "", True),
        )


def test_delete_blocks_unknown_results_and_refuses_new_input(saved):
    services, service, context, ref = saved
    service.transact(context, lambda state: state.update(requests=[unknown(context.session_id).to_dict()]))
    result = services.gui_session_commands.delete(ref, context)
    assert not result.is_success
    assert result.diagnostics[0].code == "SESSION_DELETE_PENDING"
    assert service.state(context)["session_tombstone"]
    assert services.gui_session_commands.list_sessions()
    with pytest.raises(RequestError, match="SESSION_DELETING"):
        service.accept_input(context, "do new work", selection={})
    RequestReconciliationCoordinator(service).resolve_user(
        context,
        "request",
        "effect",
        is_dispatch=False,
        expected_revision=1,
        status="cancelled",
        reference="Verified destination unchanged and original process stopped",
        command_id="resolution",
    )
    assert service.requests(service.state(context))[0].status == "cancelled"
    assert services.gui_session_commands.delete(ref, context).is_success
    with pytest.raises(Exception):
        service.update_request(context, "request", lambda r: r)


def test_delete_pending_input_and_ordinary_request_settle_without_work(saved):
    services, service, context, ref = saved
    service.accept_input(context, "pending", selection={})
    assert services.gui_session_commands.delete(ref, context).is_success
    assert not services.gui_session_commands.list_sessions()


def test_explicit_resume_renews_auto_budget_but_preserves_approval_wait():
    request = UserRequest("r", "s", "answer", (RequestItem("i", "answer"),), automatic_turns=20)
    scheduler = RequestScheduler()
    scheduler.activate("s", "view")
    assert scheduler.select_next_turn("s", "view", (request,)) is None
    resumed = reduce_request(request, RequestEvent("resume", "resume", 1))
    assert scheduler.select_next_turn("s", "view", (resumed,)) is not None
    waiting = replace(request, items=(replace(request.items[0], status="waiting", waiting_reasons=("approval",)),))
    assert reduce_request(waiting, RequestEvent("resume", "resume", 1)).items[0].waiting_reasons == ("approval",)


def test_deletion_tombstone_is_idempotent():
    state = prepare_session_deletion({"requests": []}, "s", "original")
    assert prepare_session_deletion(state, "s", "retry") == state
