from dataclasses import replace

import pytest

from tests.application.assistant_context.test_context_review import _admit
from tests.application.assistant_context.test_history_queries import _request
from transbridge.application.assistant_context.admission import (
    available_requests,
    material_digest,
    publish_context,
)
from transbridge.application.assistant_context.models import PreparationWait
from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_requests.models import RequestError
from transbridge.persistence.assistant_context_store import AssistantContextStore

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def test_two_staged_candidates_only_one_can_publish_and_business_state_is_unchanged(composed):
    _, service, context = composed
    identity = _request(service, context)
    request, admission = _admit(service, context, identity)
    before = service.state(context)
    store = AssistantContextStore(service.transcript_store)
    epoch = append_context([], request, {}, config_digest="test")
    first = store.stage(epoch)
    competing = store.stage(replace(epoch, epoch_id="competing"))
    publish_context(service, context, admission, first, None)
    with pytest.raises(PreparationWait, match="CONTEXT_HEAD_CONFLICT"):
        publish_context(service, context, admission, competing, None)
    after = service.state(context)
    assert after["context_heads"][identity] == first.head
    assert after["requests"] == before["requests"]
    assert set(r.path for r in first.references)


def test_cancelled_lease_and_configuration_change_cannot_publish(composed):
    _, service, context = composed
    identity = _request(service, context)
    request, admission = _admit(service, context, identity)
    candidate = AssistantContextStore(service.transcript_store).stage(
        append_context([], request, {}, config_digest="test")
    )
    with pytest.raises(PreparationWait, match="CONTEXT_PREPARATION_INTERRUPTED"):
        publish_context(service, context, admission, candidate, None, still_current=lambda: False)
    service.scheduler.release(admission)
    with pytest.raises(RequestError, match="TURN_LEASE_STALE"):
        publish_context(service, context, admission, candidate, None)
    assert not service.state(context).get("context_heads")


def test_material_changes_unblock_wait_but_lease_or_automatic_turn_churn_does_not(composed):
    _, service, context = composed
    identity = _request(service, context)
    request, _ = _admit(service, context, identity)
    state = service.state(context)
    state["context_waits"] = {
        identity: {
            "request_revision": request.revision,
            "config_digest": "config",
            "material_digest": material_digest(state, request),
        }
    }
    assert available_requests(state, (request,), "config") == ()
    changed_lease = replace(request, lease_epoch=request.lease_epoch + 1, automatic_turns=19)
    assert available_requests(state, (changed_lease,), "config") == ()
    state["recorded_ids"] = [*state.get("recorded_ids", []), "new-tool-result"]
    assert available_requests(state, (request,), "config") == (request,)
