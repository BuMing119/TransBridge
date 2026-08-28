from __future__ import annotations

from datetime import UTC, datetime

from transbridge.application.terminology.drafts import (
    DraftLineState,
    DraftService,
    DraftTransactionPort,
    DraftWriteExpectation,
)
from transbridge.application.terminology.errors import (
    ActiveDraftError,
    RepositoryConflictError,
    RevisionConflictError,
)
from transbridge.application.terminology.models import DraftRef, TerminologyDraft


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"story07-id-{self.value}"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 28, 9, 30, tzinfo=UTC)


class TrustedActors:
    def resolve(self, context):
        from transbridge.application.terminology.decisions import ManualActor

        return ManualActor(f"human:{context.owner_id}", trusted=True)


class DraftTransactions(DraftTransactionPort):
    def __init__(self, line: DraftLineState) -> None:
        self.line = line
        self.draft: TerminologyDraft | None = None
        self.reconciliation = ()

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        if self.draft is None:
            return None
        if (self.draft.ref.project_id, self.draft.ref.variant_id) != (project_id, variant_id):
            return None
        return self.draft

    def create_draft(self, draft, *, expected_line, historical_base):
        self._line(expected_line)
        if self.draft is not None:
            raise ActiveDraftError("active draft exists")
        if not historical_base and (
            draft.ref.base_version_id != self.line.effective_version_id
            or draft.ref.base_content_digest != self.line.effective_content_digest
        ):
            raise RepositoryConflictError("ordinary base is stale")
        self.draft = draft
        return draft.ref

    def save_draft(self, draft, *, expectation):
        current = self._expect(expectation)
        if draft.ref.draft_id != current.ref.draft_id:
            raise ActiveDraftError("save cannot change draft identity")
        if draft.ref.revision != current.ref.revision + 1:
            raise RevisionConflictError(current.ref.revision + 1, draft.ref.revision)
        if (
            draft.ref.base_version_id != current.ref.base_version_id
            or draft.ref.base_content_digest != current.ref.base_content_digest
        ):
            raise RepositoryConflictError("ordinary save cannot change draft base")
        self.draft = draft
        return draft.ref

    def replace_draft(self, previous, replacement, *, expectation):
        current = self._expect(expectation)
        if current.ref != previous:
            raise RepositoryConflictError("rebase source changed")
        if replacement.ref.draft_id == previous.draft_id or replacement.ref.revision != 0:
            raise RepositoryConflictError("rebase requires a fresh draft identity")
        if (
            replacement.ref.base_version_id != self.line.effective_version_id
            or replacement.ref.base_content_digest != self.line.effective_content_digest
        ):
            raise RepositoryConflictError("rebase target is stale")
        self.draft = replacement
        return replacement.ref

    def abandon_draft(self, ref, *, expectation):
        current = self._expect(expectation)
        if current.ref != ref:
            raise RepositoryConflictError("abandon target changed")
        self.draft = None

    def save_reconciliation(self, draft, *, reconciliation, expectation):
        self.save_draft(draft, expectation=expectation)
        self.reconciliation = reconciliation

    def _expect(self, expectation: DraftWriteExpectation) -> TerminologyDraft:
        self._line(expectation.line)
        if self.draft is None:
            raise RepositoryConflictError("draft missing")
        if self.draft.ref.draft_id != expectation.draft_id:
            raise RepositoryConflictError("draft identity changed")
        if self.draft.ref.revision != expectation.draft_revision:
            raise RevisionConflictError(expectation.draft_revision, self.draft.ref.revision)
        if self.draft.ref.decision_set_digest != expectation.decision_set_digest:
            raise RepositoryConflictError("decision digest changed")
        return self.draft

    def _line(self, expected: DraftLineState) -> None:
        if self.line != expected:
            raise RepositoryConflictError("Project/Variant/effective line changed")


def line(
    *,
    variant_revision: int = 1,
    version_id: str | None = "version-1",
    digest: str = "version-content-1",
) -> DraftLineState:
    return DraftLineState("project-1", "variant-1", variant_revision, version_id, digest)


def expectation(draft: TerminologyDraft, current_line: DraftLineState) -> DraftWriteExpectation:
    return DraftWriteExpectation.from_draft(draft, current_line)


def draft_service(transactions: DraftTransactions, ids: SequenceIds | None = None) -> DraftService:
    return DraftService(transactions, ids or SequenceIds())


def ref_tuple(ref: DraftRef) -> tuple[object, ...]:
    return ref.cache_identity
