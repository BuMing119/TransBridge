from __future__ import annotations

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
from transbridge.application.terminology.conflicts import (
    ConflictResolutionCommand,
    ConflictResolutionOperation,
    ConflictService,
    EvidenceReconciler,
    EvidenceReconciliationStatus,
)
from transbridge.application.terminology.decisions import DecisionCommand, DecisionOperation, DecisionService
from transbridge.application.terminology.drafts import OpenDraftCommand
from transbridge.application.terminology.models import (
    BuildResult,
    BuildResultRef,
    BuildSummary,
    ConflictGroup,
    ConflictStatus,
    ConflictVariant,
    DecisionStatus,
    ExtractionMethod,
    ManualActionType,
    TermCandidate,
    TermScope,
)
from transbridge.application.terminology.versions import VersionMaterializer


def _setup():
    current_line = line()
    transactions = DraftTransactions(current_line)
    ids = SequenceIds()
    drafts = draft_service(transactions, ids)
    draft = drafts.open(OpenDraftCommand(current_line, "version-1", "version-content-1"))
    decisions = DecisionService(drafts, TrustedActors(), FixedClock(), ids)
    context = RequestContext("alice", project_id="project-1", variant_id="variant-1")
    conflict = ConflictGroup(
        "conflict-1",
        "project-1",
        "variant-1",
        "sword",
        (
            ConflictVariant("剑", ("candidate-1",), ("evidence-1",)),
            ConflictVariant("刀", ("candidate-2",), ("evidence-2",)),
        ),
    )
    return transactions, drafts, decisions, context, draft, current_line, conflict


@pytest.mark.parametrize(
    ("operation", "values", "status", "action_type"),
    [
        (
            ConflictResolutionOperation.UNIFY,
            {"translation": "剑"},
            ConflictStatus.UNIFIED,
            ManualActionType.RESOLVE_CONFLICT,
        ),
        (
            ConflictResolutionOperation.PLUGIN_EXCEPTION,
            {"translation": "刀", "plugin_id": "plugin-a"},
            ConflictStatus.PLUGIN_EXCEPTION,
            ManualActionType.RESOLVE_CONFLICT,
        ),
        (
            ConflictResolutionOperation.IGNORE,
            {},
            ConflictStatus.IGNORED,
            ManualActionType.IGNORE_CONFLICT,
        ),
    ],
)
def test_every_conflict_resolution_is_explicit_and_audited(operation, values, status, action_type):
    _, _, decisions, context, draft, current_line, conflict = _setup()
    result = ConflictService(decisions).resolve(
        ConflictResolutionCommand(
            operation=operation,
            conflict=conflict,
            expectation=expectation(draft, current_line),
            **values,
        ),
        context,
    )

    assert result.conflict.status is status
    assert result.draft.conflict_resolutions == (result.conflict,)
    assert result.draft.actions[-1].action_type is action_type
    if operation is ConflictResolutionOperation.IGNORE:
        assert result.draft.decisions[0].suppressed

    materialized = VersionMaterializer().from_draft(
        BuildResult(
            BuildResultRef("build-conflict", "build-content"),
            "project-1",
            "variant-1",
            BuildSummary(1, 0, 0, 1),
            conflicts=(conflict,),
        ),
        result.draft,
    )
    assert materialized.conflicts[0].status is status


def test_automatic_evidence_reconciliation_preserves_manual_value_and_never_appends_action():
    transactions, drafts, decisions, context, draft, current_line, _ = _setup()
    draft = decisions.apply(
        DecisionCommand(
            operation=DecisionOperation.ADD,
            expectation=expectation(draft, current_line),
            original="Sword",
            translation="剑",
        ),
        context,
    )
    actions = draft.actions
    conflicting = TermCandidate(
        "candidate-2",
        "Sword",
        "刀",
        "sword",
        "刀",
        ("evidence-2",),
        TermScope.project(),
        ExtractionMethod.DETERMINISTIC_NAME,
        "v1",
    )
    reconciliation = EvidenceReconciler(drafts, transactions).reconcile(
        (conflicting,),
        expectation=expectation(draft, current_line),
    )
    decision = reconciliation.draft.decisions[0]
    assert decision.translation == "剑"
    assert decision.status is DecisionStatus.REVIEW_REQUIRED
    assert reconciliation.decisions[0].status is EvidenceReconciliationStatus.NEEDS_REVIEW
    assert reconciliation.draft.actions == actions
    assert reconciliation.manual_actions_appended == 0
    assert transactions.reconciliation == reconciliation.decisions

    no_evidence = EvidenceReconciler(drafts, transactions).reconcile(
        (),
        expectation=expectation(reconciliation.draft, current_line),
    )
    assert no_evidence.draft.decisions[0].translation == "剑"
    assert no_evidence.decisions[0].status is EvidenceReconciliationStatus.NO_EVIDENCE
    assert no_evidence.decisions[0].possibly_stale
    assert no_evidence.draft.actions == actions


def test_restored_evidence_does_not_reenable_a_suppressed_manual_decision():
    transactions, drafts, decisions, context, draft, current_line, _ = _setup()
    draft = decisions.apply(
        DecisionCommand(
            operation=DecisionOperation.ADD,
            expectation=expectation(draft, current_line),
            original="Sword",
            translation="剑",
        ),
        context,
    )
    term = draft.decisions[0]
    draft = decisions.apply(
        DecisionCommand(
            operation=DecisionOperation.SUPPRESS,
            expectation=expectation(draft, current_line),
            term_id=term.term_id,
        ),
        context,
    )
    aligned = TermCandidate(
        "candidate-1",
        "Sword",
        "剑",
        "sword",
        "剑",
        ("evidence-1",),
        TermScope.project(),
        ExtractionMethod.DETERMINISTIC_NAME,
        "v1",
    )

    result = EvidenceReconciler(drafts, transactions).reconcile(
        (aligned,), expectation=expectation(draft, current_line)
    )
    assert result.draft.decisions[0].suppressed
    assert result.decisions[0].status is EvidenceReconciliationStatus.SUPPRESSED
    assert len(result.draft.actions) == 2
