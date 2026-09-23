"""Model input contains decisions, not the execution recovery ledger."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from transbridge.application.assistant_context.budget_policy import complete_groups, current_state_item
from transbridge.application.assistant_context.models import (
    FrozenContextItem,
    PreparationWait,
    encode,
    state_item,
    state_material,
)
from transbridge.application.assistant_context.projection import append_context
from transbridge.application.assistant_context.state_projection import decision_context
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectIntent,
    EffectStatus,
    Evidence,
    ExecutionDispatch,
    ItemKind,
    ItemStatus,
    RequestItem,
    UserRequest,
    digest,
)
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore


def request():
    return UserRequest(
        "r",
        "s",
        "Translate and review",
        (
            RequestItem("translate", "Translate", ItemKind.EXECUTION, status=ItemStatus.SATISFIED),
            RequestItem("review", "Review", dependencies=("translate",)),
        ),
        constraints=("Do not modify originals",),
        source_message_ids=("u",),
    )


def admission(*ids):
    return SimpleNamespace(ready_item_ids=ids)


def execution():
    return AssistantExecutionRef("r", 1, ("translate",), "attempt", "dispatch", "turn", "s")


def history():
    return [
        {"message_id": "system", "role": "system", "content": "Rules"},
        {"message_id": "u", "role": "user", "content": "Translate and review", "request_id": "r"},
    ]


def test_answer_material_preserves_dependencies_constraints_and_captured_selection():
    original = request()
    captured = {"selected_entry_ids": ["key-a"], "active_version_identity": ["project", "v1"]}
    state = {"ingress": [{"message_id": "u", "selection": captured}, {"message_id": "other", "selection": {}}]}
    view = decision_context(original, admission("review"), state)
    assert view["purpose"] == "answer"
    assert view["constraints"] == ["Do not modify originals"]
    assert [item["item_id"] for item in view["active_items"]] == ["review"]
    assert view["dependency_status"] == {"translate": "satisfied"}
    assert view["settled_counts"] == {"satisfied": 1}
    assert view["ready_item_ids"] == ["review"]
    assert view["selection"] == [captured]
    assert "report_answer_coverage" in view["answer_protocol"]
    assert not {"effects", "evidence", "dispatches", "items"} & view.keys()
    assert original == request()


def test_completed_receipts_and_internal_progress_do_not_append_new_material():
    original = request()
    effect = EffectIntent("e", "operation", execution(), 1, status=EffectStatus.SUCCEEDED, receipt="large" * 10000)
    expanded = replace(
        original,
        effects=tuple(replace(effect, effect_id=f"e{n}") for n in range(200)),
        evidence=tuple(Evidence(f"ev{n}", "execution", 1, ("translate",), effect.receipt) for n in range(200)),
        dispatches=(ExecutionDispatch("d", execution(), "job", "run", status="completed"),),
        automatic_turns=100,
    )
    before = decision_context(original, admission("review"), {})
    after = decision_context(expanded, admission("review"), {})
    assert before == after
    first = append_context(history(), original, before, config_digest="c")
    next_epoch = append_context(history(), expanded, after, config_digest="c", previous=first)
    assert next_epoch == first
    assert len(expanded.effects) == 200 and expanded.effects[0].receipt == effect.receipt


def test_completed_item_details_do_not_grow_active_material():
    original = request()
    expanded = replace(
        original,
        items=original.items
        + tuple(
            RequestItem(f"done{n}", "Large historical description" * 100, status=ItemStatus.SATISFIED)
            for n in range(1000)
        ),
    )
    before = decision_context(original, admission("review"), {})
    after = decision_context(expanded, admission("review"), {})
    assert after["settled_counts"] == {"satisfied": 1001}
    assert after["active_items"] == before["active_items"]
    assert len(encode(after)) - len(encode(before)) < 10


def test_failed_unknown_and_running_facts_remain_visible_without_recovery_details():
    original = request()
    effect = EffectIntent("unknown", "operation", execution(), 1, status=EffectStatus.OUTCOME_UNKNOWN)
    original = replace(
        original,
        items=(
            replace(original.items[0], status=ItemStatus.WAITING, waiting_reasons=("outcome_unknown",)),
            replace(original.items[1], status=ItemStatus.FAILED),
        ),
        effects=(effect,),
        dispatches=(ExecutionDispatch("d", execution(), "job", "run", status="running", started_steps=("1",)),),
    )
    before = decision_context(original, admission("translate"), {})
    assert before["purpose"] == "execute"
    assert "answer_protocol" not in before
    assert before["active_items"][0]["waiting_reasons"] == ("outcome_unknown",)
    assert before["active_items"][1]["status"] == ItemStatus.FAILED
    assert before["unsettled_operations"][0]["status"] == "outcome_unknown"
    assert before["unsettled_dispatches"][0]["job_id"] == "job"
    advanced = replace(
        original, dispatches=(replace(original.dispatches[0], last_sequence=99, started_steps=("1", "2")),)
    )
    assert decision_context(advanced, admission("translate"), {}) == before


def test_sequence_survives_configuration_rebuild_and_artifact_reopen(tmp_path):
    original = request()
    first = append_context(history(), original, {"goal": "a"}, config_digest="c")
    second = append_context(history(), original, {"goal": "b"}, config_digest="c", previous=first)
    assert second.messages[: len(first.messages)] == first.messages
    assert [state_material(i)[0] for i in second.items if i.kind == "state"] == [1, 2]
    rebuilt = append_context(history(), original, {"goal": "b"}, config_digest="new", previous=second)
    store = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    staged = store.stage(first)
    staged = store.stage(second, staged)
    staged = store.stage(rebuilt, staged)
    restored = store.read("s", "r", (), staged.head).epoch
    assert [state_material(i)[0] for i in restored.items if i.kind == "state"] == [3]
    final = append_context(history(), original, {"goal": "c"}, config_digest="new", previous=restored)
    assert max(state_material(i)[0] for i in final.items if i.kind == "state") == 4


def test_legacy_state_is_explicitly_superseded_without_rewriting_prefix():
    original = request()
    payload = {"goal": "old"}
    legacy = FrozenContextItem(
        "legacy",
        digest(payload),
        encode({
            "role": "user",
            "content": encode({"kind": "current_request_state", "request_state": payload}),
        }),
        "legacy",
        "state",
    )
    first = append_context(history(), original, payload, config_digest="c")
    first = replace(first, items=(legacy,) + tuple(i for i in first.items if i.kind != "state"))
    after = append_context(history(), original, payload, config_digest="c", previous=first)
    assert after.messages[: len(first.messages)] == first.messages
    material = json.loads(after.messages[-1]["content"])
    assert material["state_seq"] == 1
    assert "unversioned" in material["authority"]
    assert material["decision_context"] == payload


def test_corrupt_or_duplicate_sequences_fail_visibly_instead_of_rebuilding():
    original = request()
    first = append_context(history(), original, {}, config_digest="c")
    duplicate = replace(first, items=first.items + (state_item({"goal": "b"}),))
    with pytest.raises(PreparationWait, match="CONTEXT_STATE_INVALID"):
        duplicate.validate()
    message = first.items[0].message
    material = json.loads(message["content"])
    material["state_seq"] = "invalid"
    message["content"] = encode(material)
    corrupt = replace(first, items=(replace(first.items[0], message_json=encode(message)),) + first.items[1:])
    with pytest.raises(PreparationWait, match="CONTEXT_STATE_INVALID"):
        append_context(history(), original, {}, config_digest="c", previous=corrupt)


def test_state_returning_to_an_earlier_value_keeps_latest_sequence_after_reordering():
    original = request()
    current = None
    for goal in ("a", "b", "a"):
        current = append_context(history(), original, {"goal": goal}, config_digest="c", previous=current)
    assert len(complete_groups(current.items)) == len(current.items)
    latest = current_state_item(current)
    assert state_material(latest)[0] == 3
    reordered = replace(current, items=(latest,) + tuple(i for i in current.items if i != latest))
    reordered.validate()
    assert current_state_item(reordered) == latest
