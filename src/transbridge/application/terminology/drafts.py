"""Draft lifecycle and optimistic transaction contracts for terminology."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from transbridge.application.ports import IdGeneratorPort

from .errors import ActiveDraftError, RepositoryConflictError, RevisionConflictError, TerminologyNotFoundError
from .identity import canonical_digest
from .models import ConflictGroup, DraftRef, ManualAction, TermDecision, TerminologyDraft


@dataclass(frozen=True, slots=True)
class DraftLineState:
    """Authoritative state that every mutable-draft transaction must compare."""

    project_id: str
    variant_id: str
    variant_revision: int
    effective_version_id: str | None
    effective_content_digest: str

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.variant_id.strip():
            raise ValueError("draft line requires Project and Variant identities")
        if (
            isinstance(self.variant_revision, bool)
            or not isinstance(self.variant_revision, int)
            or self.variant_revision < 0
        ):
            raise ValueError("variant revision must be a non-negative integer")
        if self.effective_version_id is not None and not self.effective_version_id.strip():
            raise ValueError("effective version ID must be absent or non-empty")
        if not self.effective_content_digest.strip():
            raise ValueError("effective content digest must not be empty")


@dataclass(frozen=True, slots=True)
class DraftWriteExpectation:
    draft_id: str
    draft_revision: int
    decision_set_digest: str
    line: DraftLineState

    def __post_init__(self) -> None:
        if not self.draft_id.strip() or not self.decision_set_digest.strip():
            raise ValueError("draft expectation requires identity and decision digest")
        if isinstance(self.draft_revision, bool) or self.draft_revision < 0:
            raise ValueError("draft revision must be a non-negative integer")

    @classmethod
    def from_draft(cls, draft: TerminologyDraft, line: DraftLineState) -> DraftWriteExpectation:
        return cls(draft.ref.draft_id, draft.ref.revision, draft.ref.decision_set_digest, line)


class DraftTransactionPort(Protocol):
    """Persistence integration point for guarded, single-transaction writes.

    Implementations must compare the complete ``DraftLineState`` with current
    Project/Variant state inside the same transaction as the draft mutation.
    ``save_draft`` persists decisions and appended ManualAction rows atomically.
    """

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None: ...

    def create_draft(
        self,
        draft: TerminologyDraft,
        *,
        expected_line: DraftLineState,
        historical_base: bool,
    ) -> DraftRef: ...

    def save_draft(
        self,
        draft: TerminologyDraft,
        *,
        expectation: DraftWriteExpectation,
    ) -> DraftRef: ...

    def replace_draft(
        self,
        previous: DraftRef,
        replacement: TerminologyDraft,
        *,
        expectation: DraftWriteExpectation,
    ) -> DraftRef: ...

    def abandon_draft(self, ref: DraftRef, *, expectation: DraftWriteExpectation) -> None: ...


class DraftWriteConflict(RepositoryConflictError):
    """Conflict retaining both the caller expectation and active draft."""

    code = "DRAFT_WRITE_CONFLICT"

    def __init__(self, expectation: DraftWriteExpectation, current: DraftRef | None, message: str) -> None:
        super().__init__(message)
        self.expectation = expectation
        self.current = current


@dataclass(frozen=True, slots=True)
class OpenDraftCommand:
    line: DraftLineState
    base_version_id: str | None
    base_content_digest: str
    decisions: tuple[TermDecision, ...] = ()

    def __post_init__(self) -> None:
        if not self.base_content_digest.strip():
            raise ValueError("draft base content digest must not be empty")
        if self.base_version_id is not None and not self.base_version_id.strip():
            raise ValueError("draft base version ID must be absent or non-empty")


@dataclass(frozen=True, slots=True)
class RebaseProposal:
    source: DraftRef
    target_line: DraftLineState
    replacement: TerminologyDraft
    proposal_digest: str


class DraftService:
    def __init__(self, transactions: DraftTransactionPort, ids: IdGeneratorPort) -> None:
        self._transactions = transactions
        self._ids = ids

    def open(self, command: OpenDraftCommand) -> TerminologyDraft:
        if (
            command.base_version_id != command.line.effective_version_id
            or command.base_content_digest != command.line.effective_content_digest
        ):
            raise RepositoryConflictError("ordinary draft base must match the current effective version")
        return self._create(command, historical_base=False)

    def from_history(self, command: OpenDraftCommand) -> TerminologyDraft:
        """Create a new draft without changing or rewriting the history version."""

        return self._create(command, historical_base=True)

    def abandon(self, expectation: DraftWriteExpectation) -> None:
        draft = self._required_active(expectation.line.project_id, expectation.line.variant_id)
        self._assert_expected(draft, expectation)
        self._transactions.abandon_draft(draft.ref, expectation=expectation)

    def propose_rebase(
        self,
        expectation: DraftWriteExpectation,
        target_line: DraftLineState,
    ) -> RebaseProposal:
        draft = self._required_active(expectation.line.project_id, expectation.line.variant_id)
        self._assert_expected(draft, expectation)
        if (target_line.project_id, target_line.variant_id) != (
            draft.ref.project_id,
            draft.ref.variant_id,
        ):
            raise ValueError("rebase target must remain on the same Project/Variant line")
        replacement = new_draft(
            draft_id=self._new_identity("draft"),
            project_id=draft.ref.project_id,
            variant_id=draft.ref.variant_id,
            base_version_id=target_line.effective_version_id,
            base_content_digest=target_line.effective_content_digest,
            decisions=draft.decisions,
            actions=draft.actions,
        )
        proposal_digest = canonical_digest(
            {"source": draft.ref, "target_line": target_line, "replacement": replacement},
            namespace="terminology.draft-rebase-proposal.v1",
        )
        return RebaseProposal(draft.ref, target_line, replacement, proposal_digest)

    def commit_rebase(
        self,
        proposal: RebaseProposal,
        expectation: DraftWriteExpectation,
    ) -> TerminologyDraft:
        draft = self._required_active(expectation.line.project_id, expectation.line.variant_id)
        self._assert_expected(draft, expectation)
        if draft.ref != proposal.source:
            raise DraftWriteConflict(expectation, draft.ref, "rebase proposal no longer matches the active draft")
        if expectation.line != proposal.target_line:
            raise DraftWriteConflict(expectation, draft.ref, "rebase target line changed after proposal")
        self._transactions.replace_draft(draft.ref, proposal.replacement, expectation=expectation)
        return proposal.replacement

    def save(
        self,
        draft: TerminologyDraft,
        *,
        expectation: DraftWriteExpectation,
    ) -> TerminologyDraft:
        current = self._required_active(expectation.line.project_id, expectation.line.variant_id)
        self._assert_expected(current, expectation)
        if draft.ref.revision != expectation.draft_revision + 1:
            raise RevisionConflictError(expectation.draft_revision + 1, draft.ref.revision)
        self._transactions.save_draft(draft, expectation=expectation)
        return draft

    def active(self, project_id: str, variant_id: str) -> TerminologyDraft:
        return self._required_active(project_id, variant_id)

    def require_expected(self, draft: TerminologyDraft, expectation: DraftWriteExpectation) -> None:
        self._assert_expected(draft, expectation)

    def _create(self, command: OpenDraftCommand, *, historical_base: bool) -> TerminologyDraft:
        if self._transactions.active_draft(command.line.project_id, command.line.variant_id) is not None:
            raise ActiveDraftError("an active draft already exists for this Project/Variant line")
        draft = new_draft(
            draft_id=self._new_identity("draft"),
            project_id=command.line.project_id,
            variant_id=command.line.variant_id,
            base_version_id=command.base_version_id,
            base_content_digest=command.base_content_digest,
            decisions=command.decisions,
        )
        self._transactions.create_draft(draft, expected_line=command.line, historical_base=historical_base)
        return draft

    def _required_active(self, project_id: str, variant_id: str) -> TerminologyDraft:
        draft = self._transactions.active_draft(project_id, variant_id)
        if draft is None:
            raise TerminologyNotFoundError("active terminology draft was not found")
        return draft

    def _new_identity(self, label: str) -> str:
        value = self._ids.new_id()
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"ID generator returned an empty {label} identity")
        return value

    @staticmethod
    def _assert_expected(draft: TerminologyDraft, expectation: DraftWriteExpectation) -> None:
        if draft.ref.draft_id != expectation.draft_id:
            raise DraftWriteConflict(expectation, draft.ref, "active draft identity changed")
        if draft.ref.revision != expectation.draft_revision:
            raise RevisionConflictError(expectation.draft_revision, draft.ref.revision)
        if draft.ref.decision_set_digest != expectation.decision_set_digest:
            raise DraftWriteConflict(expectation, draft.ref, "active draft decision set changed")


def revised_draft(
    draft: TerminologyDraft,
    *,
    decisions: tuple[TermDecision, ...] | None = None,
    actions: tuple[ManualAction, ...] | None = None,
    conflict_resolutions: tuple[ConflictGroup, ...] | None = None,
    digest_context: object | None = None,
) -> TerminologyDraft:
    updated_decisions = draft.decisions if decisions is None else decisions
    updated_actions = draft.actions if actions is None else actions
    updated_conflicts = draft.conflict_resolutions if conflict_resolutions is None else conflict_resolutions
    if any(
        (decision.project_id, decision.variant_id) != (draft.ref.project_id, draft.ref.variant_id)
        for decision in updated_decisions
    ):
        raise ValueError("draft decisions must belong to the draft Project/Variant line")
    revision = draft.ref.revision + 1
    digest = _decision_digest(
        draft.ref.draft_id,
        draft.ref.base_version_id,
        draft.ref.base_content_digest,
        updated_decisions,
        updated_actions,
        updated_conflicts,
        digest_context,
    )
    return TerminologyDraft(
        DraftRef(
            draft.ref.draft_id,
            draft.ref.project_id,
            draft.ref.variant_id,
            draft.ref.base_version_id,
            draft.ref.base_content_digest,
            revision,
            digest,
        ),
        updated_decisions,
        updated_actions,
        updated_conflicts,
    )


def new_draft(
    *,
    draft_id: str,
    project_id: str,
    variant_id: str,
    base_version_id: str | None,
    base_content_digest: str,
    decisions: tuple[TermDecision, ...] = (),
    actions: tuple[ManualAction, ...] = (),
    conflict_resolutions: tuple[ConflictGroup, ...] = (),
) -> TerminologyDraft:
    if any((decision.project_id, decision.variant_id) != (project_id, variant_id) for decision in decisions):
        raise ValueError("draft decisions must belong to the draft Project/Variant line")
    digest = _decision_digest(
        draft_id,
        base_version_id,
        base_content_digest,
        decisions,
        actions,
        conflict_resolutions,
        None,
    )
    return TerminologyDraft(
        DraftRef(
            draft_id,
            project_id,
            variant_id,
            base_version_id,
            base_content_digest,
            0,
            digest,
        ),
        decisions,
        actions,
        conflict_resolutions,
    )


def _decision_digest(
    draft_id: str,
    base_version_id: str | None,
    base_content_digest: str,
    decisions: tuple[TermDecision, ...],
    actions: tuple[ManualAction, ...],
    conflict_resolutions: tuple[ConflictGroup, ...],
    context: object | None,
) -> str:
    return canonical_digest(
        {
            "draft_id": draft_id,
            "base_version_id": base_version_id,
            "base_content_digest": base_content_digest,
            "decisions": decisions,
            "actions": actions,
            "conflict_resolutions": conflict_resolutions,
            "context": context,
        },
        namespace="terminology.draft-decision-set.v1",
    )


__all__ = [
    "DraftLineState",
    "DraftService",
    "DraftTransactionPort",
    "DraftWriteConflict",
    "DraftWriteExpectation",
    "OpenDraftCommand",
    "RebaseProposal",
    "new_draft",
    "revised_draft",
]
