"""Thread-safe in-memory baseline for terminology repository contracts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import RLock
from typing import TypeVar

from .changelog_queries import build_changelog_manifest
from .conflict_queries import ConflictFilter
from .errors import (
    ActiveDraftError,
    CursorStaleError,
    DigestCollisionError,
    RepositoryConflictError,
    RevisionConflictError,
    TerminologyNotFoundError,
)
from .identity import canonical_digest
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
from .ports import Page, PageRequest, SnapshotCursor
from .reports import build_report_manifest

T = TypeVar("T")


class InMemoryTerminologyRepository:
    """Reference adapter whose behavior is shared by persistent adapters."""

    def __init__(self) -> None:
        self._builds: dict[BuildResultRef, BuildResult] = {}
        self._build_keys: dict[str, BuildResultRef] = {}
        self._drafts: dict[tuple[str, str], TerminologyDraft] = {}
        self._versions: dict[TerminologyVersionRef, TerminologyVersion] = {}
        self._effective: dict[tuple[str, str], TerminologyVersionRef] = {}
        self._reports: dict[TerminologyReportSnapshotRef, TerminologyReportSnapshot] = {}
        self._changelogs: dict[ChangeLogDocumentRef, ChangeLogDocument] = {}
        self._artifacts: dict[str, ArtifactLedgerEntry] = {}
        self._lock = RLock()

    def put_build(self, result: BuildResult) -> BuildResultRef:
        with self._lock:
            existing_ref = self._build_keys.get(result.ref.build_key)
            if existing_ref is not None:
                existing = self._builds[existing_ref]
                if existing != result:
                    raise DigestCollisionError(result.ref.build_key)
                return existing_ref
            self._builds[result.ref] = result
            self._build_keys[result.ref.build_key] = result.ref
            return result.ref

    def get_build(self, ref: BuildResultRef) -> BuildResult:
        with self._lock:
            return self._required(self._builds, ref, "build result")

    def create_draft(self, draft: TerminologyDraft) -> DraftRef:
        line = self._draft_line(draft.ref)
        with self._lock:
            if line in self._drafts:
                raise ActiveDraftError(f"an active draft already exists for {line[0]}/{line[1]}")
            if draft.ref.revision != 0:
                raise RevisionConflictError(0, draft.ref.revision)
            self._validate_draft_base(draft.ref)
            self._drafts[line] = draft
            return draft.ref

    def update_draft(self, draft: TerminologyDraft, *, expected_revision: int) -> DraftRef:
        line = self._draft_line(draft.ref)
        with self._lock:
            current = self._drafts.get(line)
            actual = None if current is None else current.ref.revision
            if current is None or actual != expected_revision:
                raise RevisionConflictError(expected_revision, actual)
            if draft.ref.draft_id != current.ref.draft_id:
                raise ActiveDraftError("updating an active draft cannot replace its identity")
            if (
                draft.ref.base_version_id != current.ref.base_version_id
                or draft.ref.base_content_digest != current.ref.base_content_digest
            ):
                raise RepositoryConflictError("draft base cannot change during an ordinary update")
            if draft.ref.revision != expected_revision + 1:
                raise RevisionConflictError(expected_revision + 1, draft.ref.revision)
            _require_append_only_actions(current.actions, draft.actions)
            self._drafts[line] = draft
            return draft.ref

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        with self._lock:
            return self._drafts.get((project_id, variant_id))

    def discard_draft(self, project_id: str, variant_id: str, *, expected_revision: int) -> None:
        line = (project_id, variant_id)
        with self._lock:
            current = self._drafts.get(line)
            actual = None if current is None else current.ref.revision
            if current is None or actual != expected_revision:
                raise RevisionConflictError(expected_revision, actual)
            del self._drafts[line]

    def publish_version(
        self,
        version: TerminologyVersion,
        *,
        expected_effective_version_id: str | None,
    ) -> TerminologyVersionRef:
        line = (version.ref.project_id, version.ref.variant_id)
        with self._lock:
            build = self._required(self._builds, version.build_ref, "build result")
            build.require_publishable()
            current_ref = self._effective.get(line)
            actual_id = None if current_ref is None else current_ref.version_id
            if actual_id != expected_effective_version_id:
                raise RepositoryConflictError(
                    f"expected effective version {expected_effective_version_id!r}, found {actual_id!r}"
                )
            if version.parent_version_id != actual_id:
                raise RepositoryConflictError("published version parent must be the current effective version")
            existing = self._versions.get(version.ref)
            if existing is not None and existing != version:
                raise DigestCollisionError(version.ref.version_id)
            if existing is None:
                self._versions[version.ref] = version
            self._effective[line] = version.ref
            return version.ref

    def get_version(self, ref: TerminologyVersionRef) -> TerminologyVersion:
        with self._lock:
            return self._required(self._versions, ref, "terminology version")

    def effective_version(self, project_id: str, variant_id: str) -> TerminologyVersion | None:
        with self._lock:
            ref = self._effective.get((project_id, variant_id))
            return None if ref is None else self._versions[ref]

    def put_report_snapshot(self, snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotRef:
        with self._lock:
            return self._put_immutable(self._reports, snapshot.ref, snapshot, "report snapshot")

    def get_report_snapshot(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshot:
        with self._lock:
            return self._required(self._reports, ref, "report snapshot")

    def get_report_manifest(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshotManifest:
        return build_report_manifest(self.get_report_snapshot(ref))

    def put_changelog(self, document: ChangeLogDocument) -> ChangeLogDocumentRef:
        with self._lock:
            return self._put_immutable(self._changelogs, document.ref, document, "changelog document")

    def get_changelog(self, ref: ChangeLogDocumentRef) -> ChangeLogDocument:
        with self._lock:
            return self._required(self._changelogs, ref, "changelog document")

    def get_changelog_manifest(self, ref: ChangeLogDocumentRef) -> ChangeLogDocumentManifest:
        return build_changelog_manifest(self.get_changelog(ref))

    def put_artifact(self, entry: ArtifactLedgerEntry) -> ArtifactLedgerEntry:
        with self._lock:
            current = self._artifacts.get(entry.artifact_id)
            if current is not None:
                if current.owner_ref != entry.owner_ref or current.kind != entry.kind:
                    raise RepositoryConflictError("artifact identity cannot change owner or kind")
                if current != entry:
                    raise RepositoryConflictError("existing artifact ledger state requires a revision CAS update")
                return current
            self._artifacts[entry.artifact_id] = entry
            return entry

    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None:
        with self._lock:
            return self._artifacts.get(artifact_id)

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry:
        with self._lock:
            current = self._artifacts.get(entry.artifact_id)
            if current is None:
                raise KeyError(f"artifact ledger entry was not found: {entry.artifact_id}")
            if current.status is not expected_status or current.revision != expected_revision:
                raise RepositoryConflictError("artifact status or revision changed during CAS update")
            if entry.revision != expected_revision + 1:
                raise RepositoryConflictError("artifact CAS update must advance revision by exactly one")
            allowed = {
                (ArtifactStatus.PENDING, ArtifactStatus.RENDERING),
                (ArtifactStatus.FAILED, ArtifactStatus.RENDERING),
                (ArtifactStatus.RENDERING, ArtifactStatus.SUCCEEDED),
                (ArtifactStatus.RENDERING, ArtifactStatus.FAILED),
            }
            if (current.status, entry.status) not in allowed:
                raise RepositoryConflictError("artifact state transition is not allowed")
            expected_retry = current.retry_count + (
                1 if current.status is ArtifactStatus.RENDERING and entry.status is ArtifactStatus.FAILED else 0
            )
            if entry.retry_count != expected_retry:
                raise RepositoryConflictError("artifact retry count does not match the state transition")
            self._artifacts[entry.artifact_id] = entry
            return entry

    def list_evidence(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[BilingualEvidence]:
        build = self.get_build(ref)
        return self._page(build.evidence, build.ref.content_digest, request, lambda item: item.evidence_id)

    def list_candidates(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[TermCandidate]:
        build = self.get_build(ref)
        return self._page(build.candidates, build.ref.content_digest, request, lambda item: item.candidate_id)

    def list_conflicts(
        self, ref: BuildResultRef, request: PageRequest = PageRequest(), *, filters: ConflictFilter = ConflictFilter()
    ) -> Page[ConflictGroup]:
        build = self.get_build(ref)
        conflicts = tuple(item for item in build.conflicts if filters.matches(item))
        return self._page(
            conflicts, build.ref.content_digest, filters.bind_request(request), lambda item: item.conflict_group_id
        )

    def list_terms(self, ref: TerminologyVersionRef, request: PageRequest = PageRequest()) -> Page[TermDecision]:
        version = self.get_version(ref)
        return self._page(version.decisions, ref.content_digest, request, lambda item: item.term_id)

    def list_manual_actions(self, ref: DraftRef, request: PageRequest = PageRequest()) -> Page[ManualAction]:
        with self._lock:
            draft = self._drafts.get(self._draft_line(ref))
            if draft is None or draft.ref != ref:
                raise TerminologyNotFoundError("draft is not active at the requested identity and revision")
        return self._page(draft.actions, ref.decision_set_digest, request, lambda item: item.action_id)

    def list_report_terms(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[TermDecision]:
        snapshot = self.get_report_snapshot(ref)
        return self._page(snapshot.terms, ref.content_digest, request, lambda item: item.term_id)

    def list_report_conflicts(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ConflictGroup]:
        snapshot = self.get_report_snapshot(ref)
        return self._page(snapshot.conflicts, ref.content_digest, request, lambda item: item.conflict_group_id)

    def list_report_manual(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ManualAction]:
        snapshot = self.get_report_snapshot(ref)
        return self._page(snapshot.manual_actions, ref.content_digest, request, lambda item: item.action_id)

    def list_changelog_messages(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[tuple[str, tuple[str, ...]]]:
        return self._page_indexed(self.get_changelog(ref).user_messages, ref.content_digest, request)

    def list_changelog_changes(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[CanonicalChange]:
        return self._page(self.get_changelog(ref).changes, ref.content_digest, request, lambda item: item.change_id)

    def list_changelog_diagnostics(self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()) -> Page[str]:
        return self._page_indexed(self.get_changelog(ref).diagnostics, ref.content_digest, request)

    def list_changelog_conflict_group_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(self.get_changelog(ref).conflict_group_ids, ref.content_digest, request, str)

    def list_changelog_no_evidence_term_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(self.get_changelog(ref).no_evidence_term_ids, ref.content_digest, request, str)

    def list_changelog_manual_action_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(self.get_changelog(ref).manual_action_ids, ref.content_digest, request, str)

    def list_versions(
        self, project_id: str, variant_id: str, request: PageRequest = PageRequest()
    ) -> Page[TerminologyVersionRef]:
        with self._lock:
            refs = tuple(
                sorted(
                    (ref for ref in self._versions if ref.project_id == project_id and ref.variant_id == variant_id),
                    key=lambda ref: ref.version_id,
                )
            )
        digest = canonical_digest(refs, namespace="terminology.version-list.v1")
        return self._page(refs, digest, request, lambda ref: ref.version_id)

    def _validate_draft_base(self, ref: DraftRef) -> None:
        effective = self._effective.get(self._draft_line(ref))
        if effective is None:
            if ref.base_version_id is not None:
                raise RepositoryConflictError("a first draft cannot name a base version")
            return
        if ref.base_version_id != effective.version_id or ref.base_content_digest != effective.content_digest:
            raise RepositoryConflictError("draft base must match the effective version identity and content")

    @staticmethod
    def _draft_line(ref: DraftRef) -> tuple[str, str]:
        return (ref.project_id, ref.variant_id)

    @staticmethod
    def _required(mapping: dict[T, object], key: T, label: str):
        try:
            return mapping[key]
        except KeyError as exc:
            raise TerminologyNotFoundError(f"{label} was not found") from exc

    @staticmethod
    def _put_immutable(mapping: dict[T, object], key: T, value: object, label: str):
        existing = mapping.get(key)
        if existing is not None and existing != value:
            raise DigestCollisionError(f"{label}:{key!r}")
        mapping[key] = value
        return key

    @staticmethod
    def _page(
        items: Sequence[T],
        snapshot_digest: str,
        request: PageRequest,
        stable_id: Callable[[T], str],
    ) -> Page[T]:
        ordered = tuple(sorted(items, key=stable_id))
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != snapshot_digest or cursor.query_fingerprint != request.query_fingerprint
        ):
            raise CursorStaleError("cursor does not belong to this snapshot and query")
        after = None if cursor is None else (cursor.sort_values, cursor.stable_id)
        eligible = (
            ordered
            if after is None
            else tuple(item for item in ordered if ((stable_id(item),), stable_id(item)) > after)
        )
        page_items = eligible[: request.limit]
        has_more = len(eligible) > len(page_items)
        next_cursor = None
        if has_more and page_items:
            final_id = stable_id(page_items[-1])
            next_cursor = SnapshotCursor(snapshot_digest, request.query_fingerprint, (final_id,), final_id)
        return Page(tuple(page_items), snapshot_digest, next_cursor, len(ordered))

    @classmethod
    def _page_indexed(
        cls,
        items: Sequence[T],
        snapshot_digest: str,
        request: PageRequest,
    ) -> Page[T]:
        indexed = tuple((f"{index:012d}", item) for index, item in enumerate(items))
        page = cls._page(indexed, snapshot_digest, request, lambda item: item[0])
        return Page(tuple(item for _, item in page.items), page.snapshot_digest, page.next_cursor, page.total)


def _require_append_only_actions(current: tuple[ManualAction, ...], updated: tuple[ManualAction, ...]) -> None:
    updated_by_id = {item.action_id: item for item in updated}
    if any(updated_by_id.get(item.action_id) != item for item in current):
        raise RepositoryConflictError("draft ManualAction history is append-only")


__all__ = ["InMemoryTerminologyRepository"]
