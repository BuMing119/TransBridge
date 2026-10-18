from dataclasses import replace

import pytest

from tests.application.terminology.story07_support import expectation
from tests.application.terminology.test_decisions import _apply, _setup
from transbridge.application.terminology.batch_edits import batch_commands
from transbridge.application.terminology.decisions import DecisionCommand, DecisionOperation
from transbridge.application.terminology.models import DecisionStatus, ManualActionType, TermScope


def _editable():
    transactions, service, context, draft, line = _setup()
    draft = _apply(service, context, draft, line, DecisionOperation.ADD, original="Dragon", translation="龙")
    return transactions, service, context, draft, line


def _edit(service, context, draft, line, **values):
    decision = draft.decisions[0]
    fields = {
        "original": decision.original,
        "translation": decision.translation,
        "scope": decision.scope,
        "variants": decision.variants,
        "notes": decision.notes,
    }
    fields.update(values)
    return _apply(service, context, draft, line, DecisionOperation.EDIT, term_id=decision.term_id, **fields)


def test_complete_edit_replaces_identity_once_and_keeps_all_fields_and_audit():
    transactions, service, context, draft, line = _editable()
    original = draft.decisions[0]
    updated = _edit(
        service,
        context,
        draft,
        line,
        original="Ancient Dragon",
        translation="古龙",
        scope=TermScope.plugin("Dragons.esp"),
        variants=("Ancient Dragons",),
        notes="仅用于古龙种族",
    )
    replacement = next(item for item in updated.decisions if item.term_id != original.term_id)
    assert replacement.original == "Ancient Dragon"
    assert replacement.translation == "古龙"
    assert replacement.scope == TermScope.plugin("Dragons.esp")
    assert replacement.variants == ("Ancient Dragons",)
    assert replacement.notes == "仅用于古龙种族"
    assert replacement.replacement_of == original.term_id
    assert replacement.evidence_ids == ()
    assert next(item for item in updated.decisions if item.term_id == original.term_id).suppressed
    assert updated.ref.revision == draft.ref.revision + 1
    assert len(updated.actions) == len(draft.actions) + 1
    assert updated.actions[-1].action_type is ManualActionType.REPLACE_ORIGINAL
    assert updated.actions[-1].replacement_term_id == replacement.term_id
    assert transactions.draft == updated


def test_display_spelling_edit_preserves_identity_and_suppression():
    _, service, context, draft, line = _editable()
    draft = _apply(service, context, draft, line, DecisionOperation.SUPPRESS, term_id=draft.decisions[0].term_id)
    updated = _edit(service, context, draft, line, original="DRAGON", notes="拼写调整")
    assert len(updated.decisions) == 1
    assert updated.decisions[0].term_id == draft.decisions[0].term_id
    assert updated.decisions[0].original == "DRAGON"
    assert updated.decisions[0].suppressed


@pytest.mark.parametrize("collision", ["original", "scope"])
@pytest.mark.parametrize("suppressed", [False, True])
def test_identity_collision_rejects_entire_complete_edit(collision, suppressed):
    transactions, service, context, draft, line = _editable()
    draft = _apply(
        service,
        context,
        draft,
        line,
        DecisionOperation.ADD,
        original="Dragon" if collision == "scope" else "Wolf",
        translation="其他译名",
        scope=TermScope.plugin("Other.esp") if collision == "scope" else TermScope.project(),
    )
    selected = next(item for item in draft.decisions if item.scope == TermScope.project() and item.original == "Dragon")
    if suppressed:
        other = next(item for item in draft.decisions if item.term_id != selected.term_id)
        draft = _apply(service, context, draft, line, DecisionOperation.SUPPRESS, term_id=other.term_id)
    with pytest.raises(ValueError, match="已存在"):
        _apply(
            service,
            context,
            draft,
            line,
            DecisionOperation.EDIT,
            term_id=selected.term_id,
            original="Dragon" if collision == "scope" else "Wolf",
            translation="不应保存",
            scope=TermScope.plugin("Other.esp") if collision == "scope" else TermScope.project(),
            variants=(),
            notes="不应保存",
        )
    assert transactions.draft == draft


@pytest.mark.parametrize("field", ["original", "scope"])
@pytest.mark.parametrize("complete", [True, False])
def test_identity_can_return_to_its_own_suppressed_history(field, complete):
    transactions, service, context, draft, line = _editable()
    first = draft.decisions[0]
    changed = "Ancient Dragon" if field == "original" else TermScope.plugin("Dragons.esp")
    for value in (changed, getattr(first, field), changed, getattr(first, field)):
        selected = next(item for item in draft.decisions if not item.suppressed)
        previous_actions = draft.actions
        if complete:
            values = dict(
                original=selected.original, translation="人工译名", scope=selected.scope, variants=(), notes="新备注"
            )
            values[field] = value
            operation = DecisionOperation.EDIT
        else:
            values = {field: value}
            operation = DecisionOperation.REPLACE_ORIGINAL if field == "original" else DecisionOperation.CHANGE_SCOPE
        draft = _apply(service, context, draft, line, operation, term_id=selected.term_id, **values)
        assert draft.actions[:-1] == previous_actions
        assert draft.actions[-1].term_id == selected.term_id
        assert draft.actions[-1].replacement_term_id == next(
            item.term_id for item in draft.decisions if not item.suppressed
        )
        assert len(draft.decisions) == 2
        _assert_replacement_paths_end_at_current(draft)
    assert next(item for item in draft.decisions if not item.suppressed).term_id == first.term_id
    assert transactions.draft == draft


def test_restoring_middle_identity_keeps_older_identities_connected_without_cycles():
    _, service, context, draft, line = _editable()
    for original in ("Ancient Dragon", "Elder Dragon", "Ancient Dragon", "Dragon"):
        selected = next(item for item in draft.decisions if not item.suppressed)
        draft = _apply(
            service,
            context,
            draft,
            line,
            DecisionOperation.EDIT,
            term_id=selected.term_id,
            original=original,
            translation="当前人工译名",
            scope=selected.scope,
            variants=("Current alias",),
            notes="当前备注",
        )
        _assert_replacement_paths_end_at_current(draft)
    current = next(item for item in draft.decisions if not item.suppressed)
    assert (current.original, current.translation, current.variants, current.notes) == (
        "Dragon",
        "当前人工译名",
        ("Current alias",),
        "当前备注",
    )
    assert len(draft.decisions) == 3


def _assert_replacement_paths_end_at_current(draft):
    active = next(item for item in draft.decisions if not item.suppressed)
    for selected in draft.decisions:
        visited = {selected.term_id}
        while next_items := [item for item in draft.decisions if item.replacement_of == selected.term_id]:
            assert len(next_items) == 1
            selected = next_items[0]
            assert selected.term_id not in visited
            visited.add(selected.term_id)
        assert selected == active


def test_batch_validates_all_operations_before_a_single_save():
    transactions, service, context, draft, line = _editable()
    commands = (
        DecisionCommand(operation="suppress", expectation=expectation(draft, line), term_id=draft.decisions[0].term_id),
        DecisionCommand(operation="suppress", expectation=expectation(draft, line), term_id="missing"),
    )
    with pytest.raises(KeyError):
        service.apply_many(commands, context)
    assert transactions.draft == draft


def test_batch_confirm_rejects_unresolved_without_changing_other_terms():
    transactions, service, context, draft, line = _editable()
    pending = replace(draft.decisions[0], status=DecisionStatus.UNRESOLVED)
    draft = replace(draft, decisions=(pending,))
    transactions.draft = draft
    with pytest.raises(ValueError, match="异译"):
        batch_commands(draft, expectation(draft, line), (pending.term_id,), "confirm")
    assert transactions.draft == draft


def test_batch_suppression_saves_one_revision_and_an_action_for_each_term():
    _, service, context, draft, line = _editable()
    draft = _apply(service, context, draft, line, DecisionOperation.ADD, original="Wolf", translation="狼")
    commands = batch_commands(
        draft, expectation(draft, line), tuple(item.term_id for item in draft.decisions), "suppress"
    )
    updated = service.apply_many(commands, context)
    assert updated.ref.revision == draft.ref.revision + 1
    assert all(item.suppressed for item in updated.decisions)
    assert len(updated.actions) == len(draft.actions) + 2
    restored = service.apply_many(
        batch_commands(
            updated, expectation(updated, line), tuple(item.term_id for item in updated.decisions), "reenable"
        ),
        context,
    )
    assert all(item.is_effective for item in restored.decisions)
