from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.contracts import RequestContext
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.decisions import ManualActor
from transbridge.application.terminology.drafts import (
    DraftLineState,
    DraftWriteExpectation,
    new_draft,
    revised_draft,
)
from transbridge.application.terminology.identity import term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_sync.draft_import import (
    DraftImportChoice,
    DraftImportEffect,
    DraftImportSelection,
    DraftImportStaleError,
    InboundDraftImportService,
)
from transbridge.application.terminology_sync.inbound import (
    InboundAppliedProposal,
    InboundItemDisposition,
    InboundReviewDecision,
    InboundReviewState,
    InboundReviewStatus,
    InboundTerminologyChangeSet,
    build_inbound_change_set,
)
from transbridge.application.terminology_sync.mapping import local_content
from transbridge.application.terminology_sync.models import TerminologySyncMode, TerminologySyncRunOutcome
from transbridge.application.terminology_sync.plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_REMOTE_DIGEST = "a" * 64
_LINE = DraftLineState("project-1", "variant-1", 7, "version-1", "effective-digest")


def _decision(original: str, translation: str, *, identity: str | None = None) -> TermDecision:
    return TermDecision(
        identity
        or term_id(
            project_id="project-1",
            variant_id="variant-1",
            scope=TermScope.project(),
            original=original,
        ),
        "project-1",
        "variant-1",
        original,
        original.casefold(),
        translation,
        status=DecisionStatus.ADOPTED,
    )


def _summary(original: str, translation: str) -> TerminologyContentSummary:
    return TerminologyContentSummary(original, original.casefold(), translation, "project")


def _change_set(*kinds: str) -> InboundTerminologyChangeSet:
    items = []
    remote_terms = []
    for index, kind in enumerate(kinds, 1):
        if kind == "add":
            original, translation = f"Shield {index}", "盾"
            items.append(
                TerminologySyncPlanItem(
                    f"item-add-{index}",
                    TerminologySyncAction.PROPOSE_LOCAL_ADD,
                    TerminologySyncReason.INDEPENDENT_REMOTE,
                    remote_id=index,
                    remote=_summary(original, translation),
                    requires_review=True,
                )
            )
            remote_terms.append(
                ParaTranzTerm(index, TermEntry(original, translation, "paratranz"), None, f"{index:064x}")
            )
        elif kind == "update":
            local = _decision("Sword", "剑")
            items.append(
                TerminologySyncPlanItem(
                    f"item-update-{index}",
                    TerminologySyncAction.PROPOSE_LOCAL_UPDATE,
                    TerminologySyncReason.REMOTE_CHANGED,
                    local_term_id=local.term_id,
                    remote_id=index,
                    base_digest=local_content(local).digest,
                    local=local_content(local),
                    remote=_summary("Sword", "长剑"),
                    managed=True,
                    requires_review=True,
                )
            )
            remote_terms.append(ParaTranzTerm(index, TermEntry("Sword", "长剑", "paratranz"), None, f"{index:064x}"))
        elif kind == "delete":
            local = _decision("Sword", "剑")
            items.append(
                TerminologySyncPlanItem(
                    f"item-delete-{index}",
                    TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION,
                    TerminologySyncReason.REMOTE_DELETED,
                    local_term_id=local.term_id,
                    remote_id=index,
                    base_digest=local_content(local).digest,
                    local=local_content(local),
                    managed=True,
                    requires_review=True,
                )
            )
        elif kind == "conflict":
            local = _decision("Sword", "剑")
            items.append(
                TerminologySyncPlanItem(
                    f"item-conflict-{index}",
                    TerminologySyncAction.CONFLICT,
                    TerminologySyncReason.BOTH_CHANGED,
                    local_term_id=local.term_id,
                    remote_id=index,
                    local=local_content(local),
                    remote=_summary("Sword", "长剑"),
                    requires_review=True,
                )
            )
            remote_terms.append(ParaTranzTerm(index, TermEntry("Sword", "长剑", "paratranz"), None, f"{index:064x}"))
        else:  # pragma: no cover - test helper guard
            raise ValueError(kind)
    plan = TerminologySyncPlan(
        "line-1",
        "target-1",
        2,
        3,
        TerminologySyncMode.BIDIRECTIONAL,
        "project-1",
        "variant-1",
        "version-1",
        "effective-digest",
        _REMOTE_DIGEST,
        4,
        tuple(items),
    )
    snapshot = ParaTranzTermSnapshot(41, tuple(remote_terms), _REMOTE_DIGEST, _NOW, True)
    return build_inbound_change_set(
        plan,
        snapshot,
        source_run_id="run-1",
        source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
        created_at=_NOW,
    )


class _Store:
    def __init__(self, change_set: InboundTerminologyChangeSet) -> None:
        self.change_set = change_set
        self.review = InboundReviewState(change_set.change_set_id, 0, InboundReviewStatus.PENDING)
        self.fail_once = False

    def save_change_set(self, change_set):
        if change_set.change_set_id == self.change_set.change_set_id:
            return self.change_set
        raise AssertionError("unexpected change set")

    def get_change_set(self, change_set_id: str):
        assert change_set_id == self.change_set.change_set_id
        return self.change_set

    def get_review_state(self, change_set_id: str):
        assert change_set_id == self.change_set.change_set_id
        return self.review

    def commit_review(
        self,
        change_set_id: str,
        *,
        expected_revision: int,
        dispositions: tuple[InboundItemDisposition, ...],
        applied_proposal: InboundAppliedProposal,
    ) -> InboundReviewState:
        assert change_set_id == self.change_set.change_set_id
        if self.fail_once:
            self.fail_once = False
            raise OSError("synthetic disposition failure")
        if expected_revision != self.review.revision:
            raise RuntimeError("review revision conflict")
        by_id = {item.item_id: item for item in self.review.dispositions}
        by_id.update((item.item_id, item) for item in dispositions)
        proposals = {item.proposal_digest: item for item in self.review.applied_proposals}
        proposals[applied_proposal.proposal_digest] = applied_proposal
        status = (
            InboundReviewStatus.REVIEWED
            if len(by_id) == len(self.change_set.items)
            else InboundReviewStatus.PARTIALLY_REVIEWED
        )
        self.review = InboundReviewState(
            change_set_id,
            expected_revision + 1,
            status,
            tuple(by_id.values()),
            tuple(proposals.values()),
        )
        return self.review


class _DraftTransactions:
    def __init__(self, line: DraftLineState, active=None) -> None:
        self.line = line
        self.value = active
        self.create_count = 0
        self.save_count = 0

    def active_draft(self, project_id: str, variant_id: str):
        assert (project_id, variant_id) == (self.line.project_id, self.line.variant_id)
        return self.value

    def create_draft(self, draft, *, expected_line, historical_base):
        assert expected_line == self.line
        assert historical_base is False
        if self.value is not None:
            raise RuntimeError("active draft exists")
        self.value = draft
        self.create_count += 1
        return draft.ref

    def save_draft(self, draft, *, expectation):
        assert self.value is not None
        assert expectation.line == self.line
        assert self.value.ref.draft_id == expectation.draft_id
        assert self.value.ref.revision == expectation.draft_revision
        self.value = draft
        self.save_count += 1
        return draft.ref

    def replace_draft(self, previous, replacement, *, expectation):  # pragma: no cover - interface completeness
        raise NotImplementedError

    def abandon_draft(self, ref, *, expectation):  # pragma: no cover - interface completeness
        raise NotImplementedError


class _State:
    def __init__(self, line: DraftLineState, decisions: tuple[TermDecision, ...]) -> None:
        self.line = line
        self.decisions = decisions

    def current_line(self, project_id: str, variant_id: str) -> DraftLineState:
        assert (project_id, variant_id) == (self.line.project_id, self.line.variant_id)
        return self.line

    def effective_decisions(self, line: DraftLineState) -> tuple[TermDecision, ...]:
        assert line == self.line
        return self.decisions


class _Actors:
    def resolve(self, context: RequestContext) -> ManualActor:
        return ManualActor(f"trusted:{context.owner_id}", True)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"draft-{self.value}"


def _service(change_set, *, active=None, effective=()):
    store = _Store(change_set)
    transactions = _DraftTransactions(_LINE, active)
    state = _State(_LINE, tuple(effective))
    service = InboundDraftImportService(store, transactions, state, _Actors(), _Clock(), _Ids())
    return service, store, transactions, state


def _selection(change_set, choices, *, draft=None, line: DraftLineState = _LINE) -> DraftImportSelection:
    expectation = None if draft is None else DraftWriteExpectation.from_draft(draft, line)
    return DraftImportSelection(change_set.change_set_id, change_set.content_digest, 0, line, choices, expectation)


def _context() -> RequestContext:
    return RequestContext("owner-1", project_id="project-1", variant_id="variant-1")


def test_preview_is_side_effect_free_and_commit_creates_audited_draft_without_publishing() -> None:
    change_set = _change_set("add")
    base = (_decision("Sword", "剑"),)
    service, store, transactions, state = _service(change_set, effective=base)
    choice = DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT)

    proposal = service.preview(_selection(change_set, (choice,)))

    assert transactions.value is None
    assert store.review.revision == 0
    assert proposal.counts == ((DraftImportEffect.ADD.value, 1),)
    assert (
        next(item for item in proposal.decisions if item.original == "Shield 1").status
        is DecisionStatus.REVIEW_REQUIRED
    )

    result = service.commit(proposal, _context())

    assert transactions.create_count == 1
    assert transactions.value.ref.base_version_id == "version-1"
    assert len(transactions.value.actions) == 1
    assert transactions.value.actions[0].actor == "trusted:owner-1"
    assert result.review_state.status is InboundReviewStatus.REVIEWED
    assert result.review_state.dispositions[0].status is InboundReviewStatus.ACCEPTED
    assert state.line == _LINE
    assert state.decisions == base


def test_existing_draft_accept_reject_edit_batch_advances_one_revision() -> None:
    change_set = _change_set("update", "add")
    sword = _decision("Sword", "剑")
    active = new_draft(
        draft_id="draft-existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, store, transactions, _ = _service(change_set, active=active, effective=(sword,))
    update_item = next(item for item in change_set.items if "update" in item.item_id)
    add_item = next(item for item in change_set.items if "add" in item.item_id)
    choices = (
        DraftImportChoice(update_item.item_id, InboundReviewDecision.EDIT, _summary("Sword", "宝剑")),
        DraftImportChoice(add_item.item_id, InboundReviewDecision.REJECT, reason="not project terminology"),
    )

    proposal = service.preview(_selection(change_set, choices, draft=active))
    result = service.commit(proposal, _context())

    assert transactions.save_count == 1
    assert result.draft_ref.revision == active.ref.revision + 1
    assert len(transactions.value.actions) == 1
    assert next(item for item in transactions.value.decisions if item.original == "Sword").translation == "宝剑"
    statuses = {item.status for item in result.review_state.dispositions}
    assert statuses == {InboundReviewStatus.EDITED, InboundReviewStatus.REJECTED}


def test_remote_delete_is_suppression_and_never_removes_decision_or_effective_state() -> None:
    change_set = _change_set("delete")
    sword = _decision("Sword", "剑")
    active = new_draft(
        draft_id="draft-existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, _, transactions, state = _service(change_set, active=active, effective=(sword,))
    choice = DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT)

    result = service.commit(service.preview(_selection(change_set, (choice,), draft=active)), _context())

    assert len(transactions.value.decisions) == 1
    assert transactions.value.decisions[0].suppressed is True
    assert transactions.value.actions[0].action_type.value == "suppress"
    assert result.review_state.dispositions[0].after_digest
    assert state.decisions[0].suppressed is False


def test_conflict_or_normalized_duplicate_is_visible_and_not_committable() -> None:
    conflict_set = _change_set("conflict")
    sword = _decision("Sword", "剑")
    active = new_draft(
        draft_id="draft-existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, _, transactions, _ = _service(conflict_set, active=active, effective=(sword,))
    choice = DraftImportChoice(conflict_set.items[0].item_id, InboundReviewDecision.ACCEPT)
    proposal = service.preview(_selection(conflict_set, (choice,), draft=active))

    assert proposal.committable is False
    assert proposal.mutations[0].review_status is InboundReviewStatus.CONFLICT
    with pytest.raises(ValueError, match="unresolved conflicts"):
        service.commit(proposal, _context())
    assert transactions.save_count == 0

    duplicate_set = _change_set("add")
    duplicate = _decision("Shield 1", "旧盾", identity="legacy-noncanonical-id")
    duplicate_active = new_draft(
        draft_id="draft-duplicate",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(duplicate,),
    )
    service, _, _, _ = _service(duplicate_set, active=duplicate_active, effective=(duplicate,))
    proposal = service.preview(
        _selection(
            duplicate_set,
            (DraftImportChoice(duplicate_set.items[0].item_id, InboundReviewDecision.ACCEPT),),
            draft=duplicate_active,
        )
    )
    assert proposal.committable is False
    assert "normalized original conflicts" in proposal.mutations[0].diagnostic


def test_draft_revision_effective_line_and_rebase_races_are_stale() -> None:
    change_set = _change_set("update")
    sword = _decision("Sword", "剑")
    active = new_draft(
        draft_id="draft-existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, _, transactions, state = _service(change_set, active=active, effective=(sword,))
    selection = _selection(
        change_set,
        (DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT),),
        draft=active,
    )
    proposal = service.preview(selection)
    transactions.value = revised_draft(active, digest_context={"concurrent": True})
    with pytest.raises(DraftImportStaleError, match="active draft changed"):
        service.commit(proposal, _context())

    transactions.value = active
    state.line = replace(_LINE, variant_revision=8)
    with pytest.raises(DraftImportStaleError, match="effective terminology line changed"):
        service.commit(proposal, _context())

    old_base = new_draft(
        draft_id="draft-old",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-0",
        base_content_digest="old-effective",
        decisions=(sword,),
    )
    service, _, _, _ = _service(change_set, active=old_base, effective=(sword,))
    with pytest.raises(DraftImportStaleError, match="requires rebase"):
        service.preview(
            _selection(
                change_set,
                (DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT),),
                draft=old_base,
            )
        )


def test_repeat_commit_is_idempotent_and_disposition_failure_reconciles_stable_actions() -> None:
    change_set = _change_set("update")
    sword = _decision("Sword", "剑")
    active = new_draft(
        draft_id="draft-existing",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, store, transactions, _ = _service(change_set, active=active, effective=(sword,))
    choice = DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT)
    proposal = service.preview(_selection(change_set, (choice,), draft=active))

    first = service.commit(proposal, _context())
    repeated = service.commit(proposal, _context())

    assert first.replayed is False
    assert repeated.replayed is True
    assert transactions.save_count == 1
    assert len(transactions.value.actions) == 1

    change_set = _change_set("update")
    active = new_draft(
        draft_id="draft-reconcile",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
        decisions=(sword,),
    )
    service, store, transactions, _ = _service(change_set, active=active, effective=(sword,))
    store.fail_once = True
    proposal = service.preview(
        _selection(
            change_set,
            (DraftImportChoice(change_set.items[0].item_id, InboundReviewDecision.ACCEPT),),
            draft=active,
        )
    )
    with pytest.raises(OSError, match="synthetic disposition failure"):
        service.commit(proposal, _context())
    assert transactions.save_count == 1

    reconciled = service.commit(proposal, _context())

    assert reconciled.reconciled is True
    assert transactions.save_count == 1
    assert len(transactions.value.actions) == 1
