"""Guarded SQLite transactions for mutable terminology drafts."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Protocol

from transbridge.application.terminology.conflicts import (
    DecisionEvidenceReconciliation,
    EvidenceReconciliationStatus,
)
from transbridge.application.terminology.drafts import (
    DraftLineState,
    DraftWriteConflict,
    DraftWriteExpectation,
)
from transbridge.application.terminology.errors import (
    ActiveDraftError,
    RepositoryConflictError,
    RevisionConflictError,
)
from transbridge.application.terminology.models import DraftRef, TerminologyDraft

if TYPE_CHECKING:
    from .repository import SqliteTerminologyRepository


class DraftLineStateReader(Protocol):
    """Read the authoritative line while the terminology write lock is held."""

    def read_line(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        variant_id: str,
    ) -> DraftLineState: ...


class SqliteDraftTransactionAdapter:
    """Implement draft and reconciliation ports with one ``BEGIN IMMEDIATE`` guard."""

    def __init__(self, repository: SqliteTerminologyRepository, line_reader: DraftLineStateReader) -> None:
        self._repository = repository
        self._line_reader = line_reader

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        with self._repository._lock:
            return self._repository._drafts.active(project_id, variant_id)

    def create_draft(
        self,
        draft: TerminologyDraft,
        *,
        expected_line: DraftLineState,
        historical_base: bool,
    ) -> DraftRef:
        self._require_same_line(draft.ref, expected_line)
        with self._repository._lock, self._repository.transaction():
            self._require_line(expected_line)
            if not historical_base and (
                draft.ref.base_version_id != expected_line.effective_version_id
                or draft.ref.base_content_digest != expected_line.effective_content_digest
            ):
                raise RepositoryConflictError("ordinary draft base does not match the expected effective version")
            return self._repository._drafts.create(draft, historical_base=historical_base)

    def save_draft(
        self,
        draft: TerminologyDraft,
        *,
        expectation: DraftWriteExpectation,
    ) -> DraftRef:
        with self._repository._lock, self._repository.transaction():
            current = self._require_expectation(expectation)
            self._require_same_identity_and_base(current, draft)
            return self._repository._drafts.update(draft, expected_revision=expectation.draft_revision)

    def replace_draft(
        self,
        previous: DraftRef,
        replacement: TerminologyDraft,
        *,
        expectation: DraftWriteExpectation,
    ) -> DraftRef:
        with self._repository._lock, self._repository.transaction():
            current = self._require_expectation(expectation)
            if current.ref != previous:
                raise DraftWriteConflict(expectation, current.ref, "replacement source changed")
            self._require_same_line(replacement.ref, expectation.line)
            if (
                replacement.ref.base_version_id != expectation.line.effective_version_id
                or replacement.ref.base_content_digest != expectation.line.effective_content_digest
            ):
                raise RepositoryConflictError("replacement base does not match the current effective version")
            return self._repository._drafts.replace(previous, replacement)

    def abandon_draft(self, ref: DraftRef, *, expectation: DraftWriteExpectation) -> None:
        with self._repository._lock, self._repository.transaction():
            current = self._require_expectation(expectation)
            if current.ref != ref:
                raise DraftWriteConflict(expectation, current.ref, "abandon target changed")
            self._repository._drafts.discard(
                ref.project_id,
                ref.variant_id,
                expected_revision=expectation.draft_revision,
            )

    def save_reconciliation(
        self,
        draft: TerminologyDraft,
        *,
        reconciliation: tuple[DecisionEvidenceReconciliation, ...],
        expectation: DraftWriteExpectation,
    ) -> None:
        with self._repository._lock, self._repository.transaction():
            current = self._require_expectation(expectation)
            self._require_same_identity_and_base(current, draft)
            if draft.actions != current.actions:
                raise RepositoryConflictError("automatic evidence reconciliation cannot append ManualAction rows")
            _validate_reconciliation(draft, reconciliation)
            self._repository._drafts.update(draft, expected_revision=expectation.draft_revision)

    def _require_expectation(self, expectation: DraftWriteExpectation) -> TerminologyDraft:
        current = self._repository._drafts.active(expectation.line.project_id, expectation.line.variant_id)
        self._require_line(expectation.line, expectation=expectation, current=None if current is None else current.ref)
        if current is None:
            raise DraftWriteConflict(expectation, None, "active draft no longer exists")
        if current.ref.draft_id != expectation.draft_id:
            raise DraftWriteConflict(expectation, current.ref, "active draft identity changed")
        if current.ref.revision != expectation.draft_revision:
            raise RevisionConflictError(expectation.draft_revision, current.ref.revision)
        if current.ref.decision_set_digest != expectation.decision_set_digest:
            raise DraftWriteConflict(expectation, current.ref, "active draft decision set changed")
        return current

    def _require_line(
        self,
        expected: DraftLineState,
        *,
        expectation: DraftWriteExpectation | None = None,
        current: DraftRef | None = None,
    ) -> None:
        actual = self._line_reader.read_line(
            self._repository._connection,
            expected.project_id,
            expected.variant_id,
        )
        effective = self._repository._effective_ref(expected.project_id, expected.variant_id)
        effective_identity = None if effective is None else effective.version_id
        effective_digest_matches = effective is None or effective.content_digest == expected.effective_content_digest
        if actual != expected or effective_identity != expected.effective_version_id or not effective_digest_matches:
            if expectation is not None:
                raise DraftWriteConflict(expectation, current, "Project/Variant/effective line changed")
            raise RepositoryConflictError("Project/Variant/effective line changed")

    @staticmethod
    def _require_same_line(ref: DraftRef, line: DraftLineState) -> None:
        if (ref.project_id, ref.variant_id) != (line.project_id, line.variant_id):
            raise RepositoryConflictError("draft belongs to another Project/Variant line")

    @staticmethod
    def _require_same_identity_and_base(current: TerminologyDraft, updated: TerminologyDraft) -> None:
        if updated.ref.draft_id != current.ref.draft_id:
            raise ActiveDraftError("updating an active draft cannot replace its identity")
        if (
            updated.ref.base_version_id != current.ref.base_version_id
            or updated.ref.base_content_digest != current.ref.base_content_digest
        ):
            raise RepositoryConflictError("draft base cannot change during an ordinary update")


def _validate_reconciliation(
    draft: TerminologyDraft,
    reconciliation: tuple[DecisionEvidenceReconciliation, ...],
) -> None:
    projected = {item.term_id: item for item in reconciliation}
    decisions = {item.term_id: item for item in draft.decisions}
    if len(projected) != len(reconciliation) or set(projected) != set(decisions):
        raise RepositoryConflictError("evidence reconciliation must cover every draft decision exactly once")
    for term_id, decision in decisions.items():
        item = projected[term_id]
        if item.evidence_ids != decision.evidence_ids:
            raise RepositoryConflictError("evidence reconciliation does not match the persisted decision evidence")
        if decision.suppressed != (item.status is EvidenceReconciliationStatus.SUPPRESSED):
            raise RepositoryConflictError("evidence reconciliation suppression status does not match the decision")
        if item.status is EvidenceReconciliationStatus.NO_EVIDENCE and item.evidence_ids:
            raise RepositoryConflictError("no-evidence reconciliation cannot retain evidence IDs")


__all__ = ["DraftLineStateReader", "SqliteDraftTransactionAdapter"]
