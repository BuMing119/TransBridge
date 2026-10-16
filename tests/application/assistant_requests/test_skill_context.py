"""Selected workflows survive admission/restart without leaking across requests."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, _snapshot
from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_context.models import PreparationWait
from transbridge.application.assistant_context.projection import append_context, related_history
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.config.llm import LLMConfig
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.smart_assistant.context_budget import ContextBudget
from transbridge.smart_assistant.context_runtime import ContextRuntime
from transbridge.smart_assistant.request_model_input import RequestModelInput

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]

SKILL = {"name": "review", "display_name": "术语检查", "prompt": "SKILL_GLOSSARY_72493"}


def _route(service, context, text, *, skill=None):
    service.accept_input(context, text, selection={"entry_ids": ["original"]}, skill_context=skill)
    batch = service.prepare_batch(context)
    state = apply_routing_fixture(service, context, batch.batch_id, _proposal(batch))
    return service.requests(state)[-1]


def test_skill_admission_snapshots_both_sources_and_survives_restart(composed):
    services, service, context = composed
    supplied = dict(SKILL)
    accepted = service.accept_input(context, "Review", selection={}, command_id="skill", skill_context=supplied)
    supplied["prompt"] = "changed outside admission"
    assert accepted["skill_context"] == SKILL
    assert service.state(context)["ingress"][0]["skill_context"] == SKILL
    assert _snapshot(services, context).backend_messages()[0]["skill_context"] == SKILL
    assert service.accept_input(context, "Review", selection={}, command_id="skill", skill_context=SKILL) == accepted
    with pytest.raises(RequestError, match="input identity reused"):
        service.accept_input(context, "Review", selection={}, command_id="skill", skill_context=supplied)
    reopened = build_persistence_v2_services(
        services.root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "later"
    )
    try:
        restored = reopened.gui_session_commands.assistant_requests
        assert restored.state(context)["ingress"][0]["skill_context"] == SKILL
        source = RequestSummaryService(restored)._history(_snapshot(reopened, context), restored.state(context))[0]
        assert source["skill_context"] == SKILL
    finally:
        reopened.close()


def test_skill_is_request_scoped_and_keeps_static_rules(composed):
    services, service, context = composed
    first = _route(service, context, "Review the first file", skill=SKILL)
    second = _route(service, context, "Explain another file")
    history = list(_snapshot(services, context).backend_messages())
    history.insert(0, {"role": "system", "content": "STATIC_RULES", "message_id": "rules"})
    first_epoch = append_context(history, first, {}, config_digest="fixed")
    assert "STATIC_RULES" in first_epoch.systems_json and SKILL["prompt"] in first_epoch.systems_json
    second_epoch = append_context(history, second, {}, config_digest="fixed")
    assert "STATIC_RULES" in second_epoch.systems_json and SKILL["prompt"] not in second_epoch.systems_json
    followup = replace(second, related_to=first.request_id)
    related = related_history(history, followup, (first, followup), {})
    assert SKILL["prompt"] not in append_context(related, followup, {}, config_digest="fixed").systems_json
    other_owner = {first.source_message_ids[0]: second.request_id}
    assert (
        SKILL["prompt"]
        not in append_context(history, first, {}, config_digest="fixed", owners=other_owner).systems_json
    )


def test_skill_source_is_immutable_and_invalid_input_is_not_admitted(composed):
    services, service, context = composed
    request = _route(service, context, "Review", skill=SKILL)
    history = _snapshot(services, context).backend_messages()
    epoch = append_context(history, request, {}, config_digest="fixed")
    changed = deepcopy(history)
    next(m for m in changed if "skill_context" in m)["skill_context"]["prompt"] = "different"
    with pytest.raises(PreparationWait, match="原历史"):
        append_context(changed, request, {}, config_digest="fixed", previous=epoch)
    before = service.state(context)
    with pytest.raises(ValueError, match="技能"):
        service.accept_input(context, "invalid", selection={}, skill_context={"name": "x", "prompt": object()})
    assert service.state(context) == before


def test_explicit_restart_can_carry_multiple_skills_and_newer_selection_replaces_its_version(composed):
    services, service, context = composed
    request = _route(service, context, "Review again")
    history = list(_snapshot(services, context).backend_messages())
    source = next(m for m in history if m["message_id"] in request.source_message_ids)
    source["skill_contexts"] = [SKILL, {"name": "proofread", "prompt": "SECOND_SKILL"}]
    source["skill_context"] = {"name": "review", "prompt": "UPDATED_GLOSSARY"}
    epoch = append_context(history, request, {}, config_digest="fixed")
    assert "SECOND_SKILL" in epoch.systems_json and "UPDATED_GLOSSARY" in epoch.systems_json
    assert SKILL["prompt"] not in epoch.systems_json


def test_runtime_refresh_keeps_workspace_snapshot_and_frozen_selection(composed):
    _, service, context = composed
    request = _route(service, context, "Review", skill=SKILL)
    service.scheduler.activate(context.session_id, "test")
    admission = service.scheduler.select_next_turn(context.session_id, "test", (request,)).admission
    runtime = ContextRuntime(service, context, admission, client=object(), config=LLMConfig())
    store = AssistantContextStore(service.transcript_store)

    def prepare(workspace):
        runtime.prepare(
            RequestModelInput(
                (),
                (),
                ContextBudget(),
                request=request,
                request_state={"workspace_snapshot": workspace},
            )
        )
        head = service.state(context)["context_heads"][request.request_id]
        return store.read(context.session_id, request.request_id, request.scope, head).epoch

    first = prepare("PROJECT_A: 2 untranslated")
    second = prepare("PROJECT_B: 1 untranslated")
    assert second.epoch_id == first.epoch_id
    assert second.messages[: len(first.messages)] == first.messages
    assert first.source_digests == second.source_digests
    assert SKILL["prompt"] in second.systems_json
    from transbridge.application.assistant_context.models import state_material

    states = [state_material(i) for i in second.items if i.kind == "state"]
    latest = max(states, key=lambda pair: pair[0])[1]
    assert latest["workspace_snapshot"] == "PROJECT_B: 1 untranslated"
    assert latest["selection"] == [{"entry_ids": ["original"]}]
