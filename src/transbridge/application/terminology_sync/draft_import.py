"""Explicit preview/commit workflow for importing inbound facts into a draft."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

from transbridge.application.contracts import RequestContext
from transbridge.application.ports import ClockPort, IdGeneratorPort
from transbridge.application.terminology.decisions import ManualActor, ManualActorPort
from transbridge.application.terminology.drafts import (
    DraftLineState,
    DraftService,
    DraftTransactionPort,
    DraftWriteExpectation,
    new_draft,
    revised_draft,
)
from transbridge.application.terminology.identity import canonical_digest, normalize_original, term_id
from transbridge.application.terminology.models import (
    DecisionStatus,
    ManualAction,
    ManualActionType,
    ScopeKind,
    TermDecision,
    TerminologyDraft,
    TermScope,
)

from .draft_import_models import (
    DraftImportChoice,
    DraftImportCommitResult,
    DraftImportEffect,
    DraftImportMutation,
    DraftImportProposal,
    DraftImportSelection,
    DraftImportStaleError,
    DraftImportStatePort,
)
from .inbound import (
    InboundAppliedProposal,
    InboundChangeKind,
    InboundChangeSetStorePort,
    InboundItemDisposition,
    InboundReviewDecision,
    InboundReviewState,
    InboundReviewStatus,
    InboundTerminologyChange,
)
from .mapping import local_content


class InboundDraftImportService:
    """Batch audited inbound review without publishing an effective version."""

    def __init__(
        self,
        store: InboundChangeSetStorePort,
        draft_transactions: DraftTransactionPort,
        draft_state: DraftImportStatePort,
        actors: ManualActorPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._draft_transactions = draft_transactions
        self._draft_state = draft_state
        self._actors = actors
        self._clock = clock
        self._ids = ids
        self._drafts = DraftService(draft_transactions, ids)

    def preview(self, selection: DraftImportSelection) -> DraftImportProposal:
        change_set, review, line, _, initial = self._fresh_state(selection)
        changes = {item.item_id: item for item in change_set.items}
        existing_dispositions = {item.item_id for item in review.dispositions}
        decisions = tuple(initial)
        mutations: list[DraftImportMutation] = []
        diagnostics: list[str] = []
        for choice in selection.choices:
            change = changes.get(choice.item_id)
            if change is None:
                raise ValueError(f"selected inbound item does not exist: {choice.item_id}")
            if choice.item_id in existing_dispositions:
                raise DraftImportStaleError("selected inbound item was already reviewed")
            decisions, mutation = _preview_choice(change, choice, decisions, line)
            mutations.append(mutation)
            if mutation.diagnostic:
                diagnostics.append(f"{choice.item_id}:{mutation.diagnostic}")
        decisions = tuple(sorted(decisions, key=lambda item: item.term_id))
        initial_digest = _decision_digest(initial)
        payload = {
            "selection": selection,
            "source_review_revision": review.revision,
            "initial_decision_digest": initial_digest,
            "mutations": mutations,
            "decisions": decisions,
            "diagnostics": sorted(set(diagnostics)),
        }
        proposal_digest = canonical_digest(payload, namespace="terminology-sync.draft-import-proposal.v1")
        counts = tuple(sorted(Counter(item.effect.value for item in mutations).items()))
        return DraftImportProposal(
            selection,
            review.revision,
            initial_digest,
            tuple(mutations),
            decisions,
            counts,
            tuple(diagnostics),
            proposal_digest,
        )

    def commit(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult:
        selection = proposal.selection
        self._validate_context(selection.expected_line, context)
        review = self._store.get_review_state(selection.change_set_id)
        existing = next(
            (item for item in review.applied_proposals if item.proposal_digest == proposal.proposal_digest),
            None,
        )
        if existing is not None:
            return DraftImportCommitResult(
                proposal.proposal_digest,
                existing.draft_ref,
                review,
                replayed=True,
            )
        change_set = self._store.get_change_set(selection.change_set_id)
        self._validate_change_set(selection, change_set.content_digest, change_set.project_id, change_set.variant_id)
        if review.revision != proposal.source_review_revision:
            raise DraftImportStaleError("inbound review revision changed after preview")
        line = self._draft_state.current_line(change_set.project_id, change_set.variant_id)
        if line != selection.expected_line:
            raise DraftImportStaleError("effective terminology line changed after preview")
        current = self._draft_transactions.active_draft(line.project_id, line.variant_id)

        expected_action_ids = tuple(
            _action_id(proposal.proposal_digest, item.item_id)
            for item in proposal.mutations
            if item.action_type is not None
        )
        if (
            expected_action_ids
            and current is not None
            and set(expected_action_ids) <= {action.action_id for action in current.actions}
        ):
            return self._reconcile(proposal, change_set.items, current, review, expected_action_ids)

        self._validate_draft_expectation(selection, current, line)
        if not proposal.committable:
            raise ValueError("draft import proposal contains unresolved conflicts")
        initial = self._initial_decisions(current, line)
        if _decision_digest(initial) != proposal.initial_decision_digest:
            raise DraftImportStaleError("draft decisions changed after preview")

        actor = self._actors.resolve(context)
        if not isinstance(actor, ManualActor) or actor.trusted is not True:
            raise ValueError("inbound review actor identity is not trusted")
        occurred_at = self._clock.now()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("inbound review clock must return a timezone-aware datetime")
        occurred_at = occurred_at.astimezone(UTC)
        actions, dispositions = self._audit(
            proposal,
            change_set.items,
            actor,
            occurred_at,
            initial,
        )
        updated = self._save_batch(current, line, proposal.decisions, actions, proposal.proposal_digest)
        applied = InboundAppliedProposal(
            proposal.proposal_digest,
            None if updated is None else updated.ref,
            tuple(action.action_id for action in actions),
            occurred_at,
        )
        state = self._store.commit_review(
            selection.change_set_id,
            expected_revision=review.revision,
            dispositions=dispositions,
            applied_proposal=applied,
        )
        return DraftImportCommitResult(proposal.proposal_digest, applied.draft_ref, state)

    def _fresh_state(self, selection: DraftImportSelection):
        change_set = self._store.get_change_set(selection.change_set_id)
        self._validate_change_set(selection, change_set.content_digest, change_set.project_id, change_set.variant_id)
        review = self._store.get_review_state(selection.change_set_id)
        if review.revision != selection.expected_review_revision:
            raise DraftImportStaleError("inbound review revision changed before preview")
        if review.status is InboundReviewStatus.STALE:
            raise DraftImportStaleError("inbound change set is stale")
        line = self._draft_state.current_line(change_set.project_id, change_set.variant_id)
        if line != selection.expected_line:
            raise DraftImportStaleError("effective terminology line changed before preview")
        draft = self._draft_transactions.active_draft(line.project_id, line.variant_id)
        self._validate_draft_expectation(selection, draft, line)
        initial = self._initial_decisions(draft, line)
        return change_set, review, line, draft, initial

    @staticmethod
    def _validate_change_set(
        selection: DraftImportSelection,
        content_digest: str,
        project_id: str,
        variant_id: str,
    ) -> None:
        if content_digest != selection.change_set_content_digest:
            raise DraftImportStaleError("inbound change set content changed")
        if (project_id, variant_id) != (
            selection.expected_line.project_id,
            selection.expected_line.variant_id,
        ):
            raise ValueError("inbound change set belongs to another Project/Variant")

    @staticmethod
    def _validate_draft_expectation(
        selection: DraftImportSelection,
        draft: TerminologyDraft | None,
        line: DraftLineState,
    ) -> None:
        expectation = selection.draft_expectation
        if draft is None:
            if expectation is not None:
                raise DraftImportStaleError("expected active draft no longer exists")
            return
        if (draft.ref.base_version_id, draft.ref.base_content_digest) != (
            line.effective_version_id,
            line.effective_content_digest,
        ):
            raise DraftImportStaleError("active draft requires rebase before inbound import")
        if expectation is None:
            raise DraftImportStaleError("an active draft appeared after selection")
        if expectation.line != line or (
            draft.ref.draft_id,
            draft.ref.revision,
            draft.ref.decision_set_digest,
        ) != (
            expectation.draft_id,
            expectation.draft_revision,
            expectation.decision_set_digest,
        ):
            raise DraftImportStaleError("active draft changed after selection")

    def _initial_decisions(
        self,
        draft: TerminologyDraft | None,
        line: DraftLineState,
    ) -> tuple[TermDecision, ...]:
        if draft is not None:
            return draft.decisions
        decisions = tuple(self._draft_state.effective_decisions(line))
        if any((item.project_id, item.variant_id) != (line.project_id, line.variant_id) for item in decisions):
            raise ValueError("effective decisions belong to another Project/Variant")
        return tuple(sorted(decisions, key=lambda item: item.term_id))

    def _audit(
        self,
        proposal: DraftImportProposal,
        changes: tuple[InboundTerminologyChange, ...],
        actor: ManualActor,
        occurred_at: datetime,
        initial: tuple[TermDecision, ...],
    ) -> tuple[tuple[ManualAction, ...], tuple[InboundItemDisposition, ...]]:
        change_by_id = {item.item_id: item for item in changes}
        choices = {item.item_id: item for item in proposal.selection.choices}
        decisions = tuple(initial)
        actions: list[ManualAction] = []
        dispositions: list[InboundItemDisposition] = []
        for mutation in proposal.mutations:
            change = change_by_id[mutation.item_id]
            choice = choices[mutation.item_id]
            before = _decision_digest(decisions)
            action: ManualAction | None = None
            if mutation.action_type is not None:
                decisions = _replace_decisions_for_mutation(decisions, mutation)
                after = _decision_digest(decisions)
                action_term = mutation.before or mutation.after
                if action_term is None:
                    raise ValueError("audited draft mutation requires a term decision")
                action = ManualAction(
                    action_id=_action_id(proposal.proposal_digest, mutation.item_id),
                    term_id=action_term.term_id,
                    action_type=mutation.action_type,
                    actor=actor.actor_id,
                    occurred_at=occurred_at.isoformat(),
                    base_version_id=proposal.selection.expected_line.effective_version_id,
                    before_digest=before,
                    after_digest=after,
                    reason=choice.reason or f"ParaTranz inbound {change.kind.value}",
                    replacement_term_id=(
                        mutation.after.term_id
                        if mutation.before is not None
                        and mutation.after is not None
                        and mutation.before.term_id != mutation.after.term_id
                        else None
                    ),
                )
                actions.append(action)
            else:
                after = before
            dispositions.append(
                InboundItemDisposition(
                    item_id=mutation.item_id,
                    status=mutation.review_status,
                    actor=actor.actor_id,
                    occurred_at=occurred_at,
                    before_digest=before,
                    after_digest=after,
                    proposal_digest=proposal.proposal_digest,
                    remote_id=change.remote_id,
                    remote_revision=change.remote_revision,
                    remote_observed_digest=change.remote_observed_digest,
                    action_id=None if action is None else action.action_id,
                    reason=choice.reason,
                )
            )
        if tuple(sorted(decisions, key=lambda item: item.term_id)) != proposal.decisions:
            raise DraftImportStaleError("proposal decisions cannot be reproduced at commit")
        return tuple(actions), tuple(dispositions)

    def _save_batch(
        self,
        current: TerminologyDraft | None,
        line: DraftLineState,
        decisions: tuple[TermDecision, ...],
        actions: tuple[ManualAction, ...],
        proposal_digest: str,
    ) -> TerminologyDraft | None:
        if not actions:
            return current
        if current is None:
            draft_id = self._ids.new_id()
            if not isinstance(draft_id, str) or not draft_id.strip():
                raise ValueError("ID generator returned an empty draft identity")
            draft = new_draft(
                draft_id=draft_id,
                project_id=line.project_id,
                variant_id=line.variant_id,
                base_version_id=line.effective_version_id,
                base_content_digest=line.effective_content_digest,
                decisions=decisions,
                actions=actions,
            )
            self._draft_transactions.create_draft(draft, expected_line=line, historical_base=False)
            return draft
        expectation = DraftWriteExpectation.from_draft(current, line)
        draft = revised_draft(
            current,
            decisions=decisions,
            actions=(*current.actions, *actions),
            digest_context={"inbound_proposal": proposal_digest},
        )
        return self._drafts.save(draft, expectation=expectation)

    def _reconcile(
        self,
        proposal: DraftImportProposal,
        changes: tuple[InboundTerminologyChange, ...],
        current: TerminologyDraft,
        review: InboundReviewState,
        action_ids: tuple[str, ...],
    ) -> DraftImportCommitResult:
        actions = {item.action_id: item for item in current.actions}
        change_by_id = {item.item_id: item for item in changes}
        choice_by_id = {item.item_id: item for item in proposal.selection.choices}
        occurred_at = max(datetime.fromisoformat(actions[item].occurred_at) for item in action_ids).astimezone(UTC)
        dispositions = []
        for mutation in proposal.mutations:
            change = change_by_id[mutation.item_id]
            action = actions.get(_action_id(proposal.proposal_digest, mutation.item_id))
            dispositions.append(
                InboundItemDisposition(
                    mutation.item_id,
                    mutation.review_status,
                    action.actor if action else "reconciled-inbound-review",
                    occurred_at,
                    action.before_digest if action and action.before_digest else proposal.initial_decision_digest,
                    action.after_digest if action and action.after_digest else proposal.initial_decision_digest,
                    proposal.proposal_digest,
                    change.remote_id,
                    change.remote_revision,
                    change.remote_observed_digest,
                    action_id=None if action is None else action.action_id,
                    reason=choice_by_id[mutation.item_id].reason,
                )
            )
        applied = InboundAppliedProposal(proposal.proposal_digest, current.ref, action_ids, occurred_at)
        state = self._store.commit_review(
            proposal.selection.change_set_id,
            expected_revision=review.revision,
            dispositions=tuple(dispositions),
            applied_proposal=applied,
        )
        return DraftImportCommitResult(
            proposal.proposal_digest,
            current.ref,
            state,
            reconciled=True,
        )

    @staticmethod
    def _validate_context(line: DraftLineState, context: RequestContext) -> None:
        if (context.project_id, context.variant_id) != (line.project_id, line.variant_id):
            raise PermissionError("inbound review context does not match the Project/Variant")


def _preview_choice(
    change: InboundTerminologyChange,
    choice: DraftImportChoice,
    decisions: tuple[TermDecision, ...],
    line: DraftLineState,
) -> tuple[tuple[TermDecision, ...], DraftImportMutation]:
    current_by_id = {item.term_id: item for item in decisions}
    before = current_by_id.get(change.local_term_id) if change.local_term_id else None
    if choice.decision is InboundReviewDecision.REJECT:
        return decisions, DraftImportMutation(
            change.item_id,
            DraftImportEffect.REJECT,
            InboundReviewStatus.REJECTED,
            before,
            before,
            None,
        )
    if choice.decision is InboundReviewDecision.ACCEPT and change.kind is InboundChangeKind.REMOTE_CONFLICT:
        return decisions, _conflict(change, before, "remote conflict requires edit or rejection")
    summary = choice.edited if choice.decision is InboundReviewDecision.EDIT else change.remote
    review_status = (
        InboundReviewStatus.EDITED if choice.decision is InboundReviewDecision.EDIT else InboundReviewStatus.ACCEPTED
    )
    if change.kind is InboundChangeKind.REMOTE_DELETE and choice.decision is InboundReviewDecision.ACCEPT:
        if before is None:
            return decisions, _conflict(change, before, "local term for remote deletion no longer exists")
        if not _matches_planned_local(before, change):
            return decisions, _conflict(change, before, "local term changed after remote deletion was planned")
        if before.suppressed:
            return decisions, DraftImportMutation(
                change.item_id,
                DraftImportEffect.NO_CHANGE,
                review_status,
                before,
                before,
                None,
            )
        after = replace(before, suppressed=True, status=DecisionStatus.MANUAL_CONFIRMED)
        return _upsert(decisions, after), DraftImportMutation(
            change.item_id,
            DraftImportEffect.SUPPRESS,
            review_status,
            before,
            after,
            ManualActionType.SUPPRESS,
        )
    if summary is None:
        return decisions, _conflict(change, before, "inbound item has no committable terminology content")
    if summary.case_sensitive or summary.part_of_speech:
        return decisions, _conflict(change, before, "remote attributes are not representable in local decisions")
    if (
        before is not None
        and choice.decision is InboundReviewDecision.ACCEPT
        and not _matches_planned_local(before, change)
    ):
        return decisions, _conflict(change, before, "local term changed after inbound update was planned")
    scope = _scope(summary.scope)
    identity = term_id(
        project_id=line.project_id,
        variant_id=line.variant_id,
        scope=scope,
        original=summary.original,
    )
    duplicate = next(
        (
            item
            for item in decisions
            if item.normalized_original == summary.normalized_original
            and item.term_id not in {identity, None if before is None else before.term_id}
        ),
        None,
    )
    if duplicate is not None:
        return decisions, _conflict(change, before, "normalized original conflicts with an existing draft term")
    status = (
        DecisionStatus.REVIEW_REQUIRED
        if before is None and choice.decision is InboundReviewDecision.ACCEPT
        else DecisionStatus.MANUAL_CONFIRMED
    )
    after = TermDecision(
        term_id=identity,
        project_id=line.project_id,
        variant_id=line.variant_id,
        original=summary.original,
        normalized_original=normalize_original(summary.original),
        translation=summary.translation,
        scope=scope,
        status=status,
        suppressed=summary.suppressed,
        variants=summary.variants,
        notes=summary.note,
        replacement_of=(before.term_id if before is not None and before.term_id != identity else None),
    )
    if before is None:
        effect, action_type = DraftImportEffect.ADD, ManualActionType.ADD
        updated = _insert(decisions, after)
    elif before.term_id != after.term_id:
        effect, action_type = DraftImportEffect.UPDATE, ManualActionType.REPLACE_ORIGINAL
        updated = _insert(_upsert(decisions, replace(before, suppressed=True)), after)
    else:
        effect = DraftImportEffect.UPDATE
        action_type = (
            ManualActionType.CHANGE_TRANSLATION
            if before.translation != after.translation
            else ManualActionType.CHANGE_ATTRIBUTES
        )
        updated = _upsert(decisions, after)
    return updated, DraftImportMutation(
        change.item_id,
        effect,
        review_status,
        before,
        after,
        action_type,
    )


def _matches_planned_local(current: TermDecision, change: InboundTerminologyChange) -> bool:
    return change.local is not None and local_content(current).digest == change.local.digest


def _scope(value: str) -> TermScope:
    if value == ScopeKind.PROJECT.value:
        return TermScope.project()
    prefix = f"{ScopeKind.PLUGIN.value}:"
    if value.startswith(prefix) and value[len(prefix) :].strip():
        return TermScope.plugin(value[len(prefix) :])
    raise ValueError(f"unsupported inbound terminology scope: {value}")


def _conflict(
    change: InboundTerminologyChange,
    before: TermDecision | None,
    diagnostic: str,
) -> DraftImportMutation:
    return DraftImportMutation(
        change.item_id,
        DraftImportEffect.CONFLICT,
        InboundReviewStatus.CONFLICT,
        before,
        before,
        None,
        diagnostic,
    )


def _insert(decisions: tuple[TermDecision, ...], added: TermDecision) -> tuple[TermDecision, ...]:
    if any(item.term_id == added.term_id for item in decisions):
        raise ValueError("draft already contains the inbound term identity")
    return tuple(sorted((*decisions, added), key=lambda item: item.term_id))


def _upsert(decisions: tuple[TermDecision, ...], updated: TermDecision) -> tuple[TermDecision, ...]:
    return tuple(
        sorted(
            (updated if item.term_id == updated.term_id else item for item in decisions), key=lambda item: item.term_id
        )
    )


def _replace_decisions_for_mutation(
    decisions: tuple[TermDecision, ...],
    mutation: DraftImportMutation,
) -> tuple[TermDecision, ...]:
    if mutation.after is None:
        return decisions
    if mutation.before is None:
        return _insert(decisions, mutation.after)
    if mutation.before.term_id == mutation.after.term_id:
        return _upsert(decisions, mutation.after)
    return _insert(_upsert(decisions, replace(mutation.before, suppressed=True)), mutation.after)


def _decision_digest(decisions: tuple[TermDecision, ...]) -> str:
    return canonical_digest(decisions, namespace="terminology.manual-decision-state.v1")


def _action_id(proposal_digest: str, item_id: str) -> str:
    return canonical_digest(
        {"proposal_digest": proposal_digest, "item_id": item_id},
        namespace="terminology-sync.inbound-action.v1",
    )


__all__ = [
    "DraftImportChoice",
    "DraftImportCommitResult",
    "DraftImportEffect",
    "DraftImportMutation",
    "DraftImportProposal",
    "DraftImportSelection",
    "DraftImportStaleError",
    "DraftImportStatePort",
    "InboundDraftImportService",
]
