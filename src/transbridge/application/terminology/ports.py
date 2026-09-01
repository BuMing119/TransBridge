"""Storage, query, pagination, and time ports for project terminology."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Protocol

from .conflict_queries import ConflictFilter
from .models import (
    ArtifactLedgerEntry,
    ArtifactStatus,
    BilingualEvidence,
    BuildResult,
    BuildResultRef,
    CanonicalChange,
    ChangeLogDocument,
    ChangeLogDocumentManifest,
    ChangeLogDocumentRef,
    ConflictGroup,
    DraftRef,
    ManualAction,
    TermCandidate,
    TermDecision,
    TerminologyDraft,
    TerminologyReportSnapshot,
    TerminologyReportSnapshotManifest,
    TerminologyReportSnapshotRef,
    TerminologyVersion,
    TerminologyVersionRef,
)


@dataclass(frozen=True, slots=True)
class SnapshotCursor:
    snapshot_digest: str
    query_fingerprint: str
    sort_values: tuple[str, ...]
    stable_id: str

    def __post_init__(self) -> None:
        for name in ("snapshot_digest", "query_fingerprint", "stable_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"cursor {name.replace('_', ' ')} must not be empty")
        values = tuple(str(value) for value in self.sort_values)
        if not values:
            raise ValueError("cursor sort values must not be empty")
        object.__setattr__(self, "sort_values", values)

    def encode(self) -> str:
        payload = json.dumps(
            {
                "query_fingerprint": self.query_fingerprint,
                "snapshot_digest": self.snapshot_digest,
                "sort_values": self.sort_values,
                "stable_id": self.stable_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> SnapshotCursor:
        if not isinstance(value, str) or not value:
            raise ValueError("cursor token must not be empty")
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(value + padding))
            return cls(
                snapshot_digest=str(payload["snapshot_digest"]),
                query_fingerprint=str(payload["query_fingerprint"]),
                sort_values=tuple(str(item) for item in payload["sort_values"]),
                stable_id=str(payload["stable_id"]),
            )
        except (binascii.Error, KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid cursor token") from exc


@dataclass(frozen=True, slots=True)
class PageRequest:
    limit: int = 100
    cursor: SnapshotCursor | None = None
    query_fingerprint: str = "all"

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 1000:
            raise ValueError("page limit must be between 1 and 1000")
        if not isinstance(self.query_fingerprint, str) or not self.query_fingerprint.strip():
            raise ValueError("query fingerprint must not be empty")


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    snapshot_digest: str
    next_cursor: SnapshotCursor | None = None
    total: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if not self.snapshot_digest:
            raise ValueError("page snapshot digest must not be empty")
        if self.total is not None and (
            isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0
        ):
            raise ValueError("page total must be a non-negative integer")


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class TerminologyRepositoryPort(Protocol):
    def put_build(self, result: BuildResult) -> BuildResultRef: ...

    def get_build(self, ref: BuildResultRef) -> BuildResult: ...

    def create_draft(self, draft: TerminologyDraft) -> DraftRef: ...

    def update_draft(self, draft: TerminologyDraft, *, expected_revision: int) -> DraftRef: ...

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None: ...

    def discard_draft(self, project_id: str, variant_id: str, *, expected_revision: int) -> None: ...

    def publish_version(
        self,
        version: TerminologyVersion,
        *,
        expected_effective_version_id: str | None,
    ) -> TerminologyVersionRef: ...

    def get_version(self, ref: TerminologyVersionRef) -> TerminologyVersion: ...

    def effective_version(self, project_id: str, variant_id: str) -> TerminologyVersion | None: ...

    def put_report_snapshot(self, snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotRef: ...

    def get_report_snapshot(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshot: ...

    def get_report_manifest(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshotManifest: ...

    def put_changelog(self, document: ChangeLogDocument) -> ChangeLogDocumentRef: ...

    def get_changelog(self, ref: ChangeLogDocumentRef) -> ChangeLogDocument: ...

    def put_artifact(self, entry: ArtifactLedgerEntry) -> ArtifactLedgerEntry: ...

    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None: ...

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry: ...


class TerminologyQueryPort(Protocol):
    def list_evidence(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[BilingualEvidence]: ...

    def list_candidates(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[TermCandidate]: ...

    def list_conflicts(
        self, ref: BuildResultRef, request: PageRequest = PageRequest(), *, filters: ConflictFilter = ConflictFilter()
    ) -> Page[ConflictGroup]: ...

    def list_terms(self, ref: TerminologyVersionRef, request: PageRequest = PageRequest()) -> Page[TermDecision]: ...

    def list_manual_actions(self, ref: DraftRef, request: PageRequest = PageRequest()) -> Page[ManualAction]: ...

    def list_report_terms(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[TermDecision]: ...

    def list_report_conflicts(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ConflictGroup]: ...

    def list_report_manual(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ManualAction]: ...

    def list_versions(
        self, project_id: str, variant_id: str, request: PageRequest = PageRequest()
    ) -> Page[TerminologyVersionRef]: ...


class ChangeLogQueryPort(Protocol):
    def get_changelog_manifest(self, ref: ChangeLogDocumentRef) -> ChangeLogDocumentManifest: ...

    def list_changelog_messages(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[tuple[str, tuple[str, ...]]]: ...

    def list_changelog_changes(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[CanonicalChange]: ...

    def list_changelog_diagnostics(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_conflict_group_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_no_evidence_term_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...

    def list_changelog_manual_action_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]: ...


__all__ = [
    "ClockPort",
    "ChangeLogQueryPort",
    "Page",
    "PageRequest",
    "SnapshotCursor",
    "TerminologyQueryPort",
    "TerminologyRepositoryPort",
]
