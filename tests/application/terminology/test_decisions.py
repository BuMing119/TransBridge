from __future__ import annotations

from dataclasses import replace

import pytest

from tests.application.terminology.story07_support import (
    DraftTransactions,
    FixedClock,
    SequenceIds,
    TrustedActors,
    draft_service,
    expectation,
    line,
)
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.decisions import (
    DecisionCommand,
    DecisionOperation,
    DecisionService,
    ManualActor,
)
from transbridge.application.terminology.drafts import OpenDraftCommand
from transbridge.application.terminology.models import ManualActionType, ScopeKind, TermScope


def _setup():
    current_line = line()
    transactions = DraftTransactions(current_line)
    ids = SequenceIds()
    drafts = draft_service(transactions, ids)
    draft = drafts.open(OpenDraftCommand(current_line, "version-1", "version-content-1"))
    service = DecisionService(drafts, TrustedActors(), FixedClock(), ids)
    context = RequestContext("alice", project_id="project-1", variant_id="variant-1")
    return transactions, service, context, draft, current_line


def _apply(service, context, draft, current_line, operation, **kwargs):
    return service.apply(
        DecisionCommand(
            operation=operation,
            expectation=expectation(draft, current_line),
            **kwargs,
        ),
        context,
    )


def test_manual_edits_append_audited_actions_and_preserve_expected_revision():
    _, service, context, draft, current_line = _setup()
    draft = _apply(
        service,
        context,
        draft,
        current_line,
        DecisionOperation.ADD,
        original="Iron Sword",
        translation="铁剑",
    )
    term = draft.decisions[0]
    operations = (
        (DecisionOperation.CHANGE_TRANSLATION, {"translation": "钢剑"}),
        (DecisionOperation.CHANGE_VARIANTS, {"variants": ("female", "male")}),
        (DecisionOperation.CHANGE_NOTES, {"notes": "UI label"}),
        (DecisionOperation.SUPPRESS, {}),
        (DecisionOperation.REENABLE, {}),
    )
    for operation, values in operations:
        draft = _apply(service, context, draft, current_line, operation, term_id=term.term_id, **values)
        term = next(item for item in draft.decisions if item.term_id == term.term_id)

    assert draft.ref.revision == 6
    assert len(draft.actions) == 6
    assert {action.actor for action in draft.actions} == {"human:alice"}
    assert all(action.occurred_at.endswith("+00:00") for action in draft.actions)
    assert [action.action_type for action in draft.actions[-2:]] == [
        ManualActionType.SUPPRESS,
        ManualActionType.REENABLE,
    ]


def test_original_and_scope_changes_create_replacements_and_suppress_previous_identity():
    _, service, context, draft, current_line = _setup()
    draft = _apply(
        service,
        context,
        draft,
        current_line,
        DecisionOperation.ADD,
        original="Sword",
        translation="剑",
    )
    original = draft.decisions[0]
    draft = _apply(
        service,
        context,
        draft,
        current_line,
        DecisionOperation.REPLACE_ORIGINAL,
        term_id=original.term_id,
        original="Long Sword",
    )
    replacement = next(item for item in draft.decisions if item.replacement_of == original.term_id)
    assert next(item for item in draft.decisions if item.term_id == original.term_id).suppressed
    assert replacement.evidence_ids == ()
    assert draft.actions[-1].replacement_term_id == replacement.term_id

    draft = _apply(
        service,
        context,
        draft,
        current_line,
        DecisionOperation.CHANGE_SCOPE,
        term_id=replacement.term_id,
        scope=TermScope(ScopeKind.PLUGIN, "plugin-a"),
    )
    scoped = next(item for item in draft.decisions if item.replacement_of == replacement.term_id)
    assert scoped.scope.plugin_id == "plugin-a"
    assert next(item for item in draft.decisions if item.term_id == replacement.term_id).suppressed


def test_untrusted_or_empty_actor_is_rejected_without_manual_action():
    transactions, _, context, draft, current_line = _setup()

    class UntrustedActors:
        def resolve(self, _context):
            return replace(ManualActor("actor", True), trusted=False)

    ids = SequenceIds()
    service = DecisionService(draft_service(transactions, ids), UntrustedActors(), FixedClock(), ids)
    with pytest.raises(ValueError, match="trusted"):
        _apply(
            service,
            context,
            draft,
            current_line,
            DecisionOperation.ADD,
            original="Sword",
            translation="剑",
        )
    assert transactions.draft == draft
