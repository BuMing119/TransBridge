"""Undo provenance follows accepted user input rather than model-supplied IDs."""

from dataclasses import replace

import pytest

from tests.routing_fixtures import apply_routing_fixture

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _route(service, context, text, action, target=None):
    source = service.accept_input(context, text, selection={})
    batch = service.prepare_batch(context)
    directive = {
        "local_id": "action",
        "message_id": source["message_id"],
        "span": [0, len(text)],
        "action": action,
    }
    if action in ("CREATE", "FOLLOW_UP", "AMEND"):
        directive.update(goal=text, items=[{"item_id": "write", "description": text, "kind": "execution"}])
    if target is not None:
        directive.update(target_id=target.request_id, expected_revision=target.revision)
    state = apply_routing_fixture(service, context, batch.batch_id, {"protocol_version": 1, "directives": [directive]})
    applied = next(item["batch"] for item in state["batches"] if item["batch"]["batch_id"] == batch.batch_id)
    receipt = applied["receipts"][0]
    assert receipt["status"] == "applied", receipt
    request = next(request for request in service.requests(state) if request.request_id == receipt["request_id"])
    return request, source["message_id"]


def test_create_and_follow_up_have_distinct_real_input_rounds(composed):
    _, service, context = composed
    original, first = _route(service, context, "翻译", "CREATE")
    follow, second = _route(service, context, "然后检查", "FOLLOW_UP", original)
    current = next(r for r in service.requests(service.state(context)) if r.request_id == original.request_id)
    assert original.work_round_id == current.work_round_id == first
    assert follow.work_round_id == second and second != first
    assert follow.source_message_ids == (second,) and follow.related_to == original.request_id


@pytest.mark.parametrize("action", ["AMEND", "RESUME"])
def test_new_input_amend_or_resume_updates_round_and_preserves_its_source_membership(composed, action):
    _, service, context = composed
    original, first = _route(service, context, "翻译", "CREATE")
    if action == "RESUME":
        original = service.command(context, original.request_id, "pause", original.revision)
    changed, second = _route(service, context, "继续修改", action, original)
    assert changed.work_round_id == second and changed.source_message_ids == (first, second)
    assert changed.request_id == original.request_id
    assert changed.revision == original.revision + (1 if action == "AMEND" else 0)


def test_non_ingress_resume_preserves_existing_round(composed):
    _, service, context = composed
    original, source = _route(service, context, "翻译", "CREATE")
    paused = service.command(context, original.request_id, "pause", original.revision)
    resumed = service.command(context, original.request_id, "resume", paused.revision)
    assert resumed.work_round_id == source and resumed.source_message_ids == (source,)


def test_legacy_request_requires_new_input_to_acquire_round_provenance(composed):
    _, service, context = composed
    original, first = _route(service, context, "translate", "CREATE")
    legacy = service.update_request(context, original.request_id, lambda request: replace(request, work_round_id=""))
    paused = service.command(context, legacy.request_id, "pause", legacy.revision)
    assert paused.work_round_id == ""
    resumed, new_source = _route(service, context, "continue", "RESUME", paused)
    assert resumed.work_round_id == new_source != first
    assert resumed.source_message_ids == (first, new_source)
    assert not service.state(context).get("undo_rounds")


def test_model_cannot_supply_round_identity_in_routing(composed):
    from transbridge.application.assistant_requests.models import RequestError

    _, service, context = composed
    source = service.accept_input(context, "翻译", selection={})
    batch = service.prepare_batch(context)
    with pytest.raises(RequestError, match="REQUEST_PROTOCOL_INVALID"):
        apply_routing_fixture(
            service,
            context,
            batch.batch_id,
            {
                "protocol_version": 1,
                "directives": [
                    {
                        "local_id": "work",
                        "message_id": source["message_id"],
                        "span": [0, 2],
                        "action": "CREATE",
                        "goal": "翻译",
                        "items": [],
                        "work_round_id": "forged",
                    }
                ],
            },
        )
