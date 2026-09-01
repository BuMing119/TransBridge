"""SQLite implementation of the S02 terminology repository/query ports."""

from __future__ import annotations

from dataclasses import replace
import sqlite3
from threading import RLock
from typing import TYPE_CHECKING, Any

from transbridge.application.terminology.conflict_queries import ConflictFilter
from transbridge.application.terminology.errors import (
    CursorStaleError,
    DigestCollisionError,
    RepositoryConflictError,
    TerminologyNotFoundError,
)
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.models import (
    ArtifactLedgerEntry,
    ArtifactStatus,
    BilingualEvidence,
    BuildResult,
    BuildResultRef,
    CanonicalDiff,
    ChangeLogDocument,
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
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor

from .artifacts import ArtifactLedger
from .cache import TerminologyCache
from .changelog import ChangelogDocumentStore
from .codec import dumps, loads
from .conflict_queries import conflict_page
from .connection import (
    StorageMode,
    TerminologyConnectionFactory,
    TerminologyStorageError,
    TerminologyStorageReadOnlyError,
    translate_sqlite_error,
)
from .drafts import DraftStore
from .inbound_review import SqliteInboundReviewStore
from .paths import TerminologyPaths
from .publish import SqlitePublishRepository
from .queries import keyset_page
from .report_snapshot import SqliteReportSnapshotStore
from .sync_state import SqliteTerminologySyncState

if TYPE_CHECKING:
    from .draft_transactions import DraftLineStateReader, SqliteDraftTransactionAdapter


class SqliteTerminologyTransaction:
    """Short ``BEGIN IMMEDIATE`` unit used by repository mutations."""

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self._repository = repository
        self.connection = repository._connection

    def __enter__(self) -> SqliteTerminologyTransaction:
        self._repository._ensure_writable()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc, self._repository.storage_state) from exc
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: Any) -> bool:
        if exc is None:
            try:
                self.connection.commit()
            except sqlite3.Error as commit_error:
                self.connection.rollback()
                raise translate_sqlite_error(commit_error, self._repository.storage_state) from commit_error
            return False
        self.connection.rollback()
        if isinstance(exc, sqlite3.Error):
            raise translate_sqlite_error(exc, self._repository.storage_state) from exc
        return False


class SqliteTerminologyRepository:
    """One repository instance owns exactly one Project SQLite asset."""

    def __init__(self, factory: TerminologyConnectionFactory, project_id: str, *, writable: bool = True) -> None:
        opened = factory.open(project_id, writable=writable)
        self.project_id = project_id
        self.path = opened.path
        self.storage_state = opened.state
        self._connection = opened.connection
        self._lock = RLock()
        self.cache = TerminologyCache(self._connection)
        self.artifacts = ArtifactLedger(self._connection)
        self.changelogs = ChangelogDocumentStore(self._connection)
        self.report_snapshots = SqliteReportSnapshotStore(self._connection, build_reader=self)
        self.sync_state = SqliteTerminologySyncState(self)
        self.inbound_reviews = SqliteInboundReviewStore(self)
        self._drafts = DraftStore(
            self._connection,
            project_id,
            decode=self._decode,
            effective_ref=self._effective_ref,
        )
        self.publisher = SqlitePublishRepository(self)

    @classmethod
    def open(
        cls,
        root: str,
        project_id: str,
        *,
        allow_wal: bool = False,
        writable: bool = True,
    ) -> SqliteTerminologyRepository:
        return cls(
            TerminologyConnectionFactory(TerminologyPaths(root), allow_wal=allow_wal), project_id, writable=writable
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def transaction(self) -> SqliteTerminologyTransaction:
        return SqliteTerminologyTransaction(self)

    def draft_transactions(self, line_reader: DraftLineStateReader) -> SqliteDraftTransactionAdapter:
        from .draft_transactions import SqliteDraftTransactionAdapter

        return SqliteDraftTransactionAdapter(self, line_reader)

    def put_build(self, result: BuildResult) -> BuildResultRef:
        self._require_project(result.project_id)
        with self._lock, self.transaction():
            row = self._connection.execute(
                "SELECT payload_json FROM builds WHERE build_key = ?",
                (result.ref.build_key,),
            ).fetchone()
            if row is not None:
                existing = self._decode(str(row["payload_json"]), BuildResult)
                if existing != result:
                    raise DigestCollisionError(result.ref.build_key)
                return existing.ref
            self._connection.execute(
                "INSERT INTO builds(build_key, content_digest, project_id, variant_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (result.ref.build_key, result.ref.content_digest, result.project_id, result.variant_id, dumps(result)),
            )
            self._insert_children("build_evidence", result.ref.build_key, result.evidence, "evidence_id")
            self._insert_children("build_candidates", result.ref.build_key, result.candidates, "candidate_id")
            self._insert_children("build_conflicts", result.ref.build_key, result.conflicts, "conflict_group_id")
        return result.ref

    def get_build(self, ref: BuildResultRef) -> BuildResult:
        with self._lock:
            row = self._connection.execute(
                "SELECT content_digest, project_id, payload_json FROM builds WHERE build_key = ?",
                (ref.build_key,),
            ).fetchone()
            if row is None or str(row["content_digest"]) != ref.content_digest:
                raise TerminologyNotFoundError("build result was not found")
            self._require_project(str(row["project_id"]))
            result = self._decode(str(row["payload_json"]), BuildResult)
            if result.ref != ref:
                raise TerminologyNotFoundError("build result content digest is inconsistent")
            return result

    def latest_build(self, project_id: str, variant_id: str) -> BuildResult | None:
        """Return the newest persisted build on a Project/Variant line.

        SQLite ``rowid`` is used only as an insertion-order lookup; the
        returned immutable BuildResult remains digest-addressed.
        """

        self._require_project(project_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM builds WHERE project_id = ? AND variant_id = ? ORDER BY rowid DESC LIMIT 1",
                (project_id, variant_id),
            ).fetchone()
            return None if row is None else self._decode(str(row["payload_json"]), BuildResult)

    def create_draft(self, draft: TerminologyDraft) -> DraftRef:
        with self._lock, self.transaction():
            return self._drafts.create(draft)

    def update_draft(self, draft: TerminologyDraft, *, expected_revision: int) -> DraftRef:
        with self._lock, self.transaction():
            return self._drafts.update(draft, expected_revision=expected_revision)

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        with self._lock:
            return self._drafts.active(project_id, variant_id)

    def discard_draft(self, project_id: str, variant_id: str, *, expected_revision: int) -> None:
        with self._lock, self.transaction():
            self._drafts.discard(project_id, variant_id, expected_revision=expected_revision)

    def publish_version(
        self,
        version: TerminologyVersion,
        *,
        expected_effective_version_id: str | None,
    ) -> TerminologyVersionRef:
        self._require_project(version.ref.project_id)
        with self._lock, self.transaction():
            build_row = self._connection.execute(
                "SELECT payload_json FROM builds WHERE build_key = ? AND content_digest = ?",
                (version.build_ref.build_key, version.build_ref.content_digest),
            ).fetchone()
            if build_row is None:
                raise TerminologyNotFoundError("build result was not found")
            self._decode(str(build_row["payload_json"]), BuildResult).require_publishable()
            effective = self._effective_ref(version.ref.project_id, version.ref.variant_id)
            actual_id = None if effective is None else effective.version_id
            if actual_id != expected_effective_version_id:
                raise RepositoryConflictError(
                    f"expected effective version {expected_effective_version_id!r}, found {actual_id!r}"
                )
            if version.parent_version_id != actual_id:
                raise RepositoryConflictError("published version parent must be the current effective version")
            key = _version_key(version.ref.variant_id, version.ref.version_id)
            row = self._connection.execute(
                "SELECT payload_json FROM versions WHERE version_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                existing = self._decode(str(row["payload_json"]), TerminologyVersion)
                if existing != version:
                    raise DigestCollisionError(version.ref.version_id)
            else:
                parent_key = (
                    None
                    if version.parent_version_id is None
                    else _version_key(version.ref.variant_id, version.parent_version_id)
                )
                self._connection.execute(
                    "INSERT INTO versions(version_key, version_id, project_id, variant_id, content_digest, "
                    "parent_version_key, build_key, published_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        version.ref.version_id,
                        version.ref.project_id,
                        version.ref.variant_id,
                        version.ref.content_digest,
                        parent_key,
                        version.build_ref.build_key,
                        version.published_at,
                        dumps(version),
                    ),
                )
                self._insert_children("version_terms", key, version.decisions, "term_id", owner_column="version_key")
                self._connection.execute(
                    "INSERT INTO canonical_diffs(version_key, content_digest, payload_json) VALUES (?, ?, ?)",
                    (key, version.canonical_diff.content_digest, dumps(version.canonical_diff)),
                )
            self._connection.execute(
                "INSERT INTO effective_versions(project_id, variant_id, version_key) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, variant_id) DO UPDATE SET version_key=excluded.version_key",
                (version.ref.project_id, version.ref.variant_id, key),
            )
        return version.ref

    def get_version(self, ref: TerminologyVersionRef) -> TerminologyVersion:
        self._require_project(ref.project_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT content_digest, payload_json FROM versions WHERE version_key = ?",
                (_version_key(ref.variant_id, ref.version_id),),
            ).fetchone()
            if row is None or str(row["content_digest"]) != ref.content_digest:
                raise TerminologyNotFoundError("terminology version was not found")
            version = self._decode(str(row["payload_json"]), TerminologyVersion)
            if version.ref != ref:
                raise TerminologyNotFoundError("terminology version content digest is inconsistent")
            return version

    def effective_version(self, project_id: str, variant_id: str) -> TerminologyVersion | None:
        self._require_project(project_id)
        with self._lock:
            ref = self._effective_ref(project_id, variant_id)
            return None if ref is None else self.get_version(ref)

    def effective_version_ref(self, project_id: str, variant_id: str) -> TerminologyVersionRef | None:
        """Return the effective scalar reference without decoding its full membership."""

        self._require_project(project_id)
        with self._lock:
            return self._effective_ref(project_id, variant_id)

    def direct_canonical_diff(
        self,
        parent_ref: TerminologyVersionRef,
        target_ref: TerminologyVersionRef,
    ) -> CanonicalDiff | None:
        """Load the persisted diff when ``parent_ref`` directly precedes ``target_ref``.

        Early schema-v2 assets may not have the separately indexed diff row because the
        pre-production ``publish_version`` path only embedded it in the version payload.
        Those assets retain a read-compatible fallback through ``get_version``.
        """

        if (parent_ref.project_id, parent_ref.variant_id) != (target_ref.project_id, target_ref.variant_id):
            raise RepositoryConflictError("terminology versions belong to different Project/Variant lines")
        self._require_project(parent_ref.project_id)
        with self._lock:
            self._require_version_ref(parent_ref)
            self._require_version_ref(target_ref)
            row = self._connection.execute(
                "SELECT v.parent_version_key, d.content_digest AS diff_digest, d.payload_json AS diff_payload "
                "FROM versions v LEFT JOIN canonical_diffs d ON d.version_key = v.version_key "
                "WHERE v.version_key = ?",
                (_version_key(target_ref.variant_id, target_ref.version_id),),
            ).fetchone()
            if row is None:  # pragma: no cover - reference validation above establishes the row
                raise TerminologyNotFoundError("terminology version was not found")
            if row["parent_version_key"] != _version_key(parent_ref.variant_id, parent_ref.version_id):
                return None
            if row["diff_payload"] is None:
                return self.get_version(target_ref).canonical_diff
            diff = self._decode(str(row["diff_payload"]), CanonicalDiff)
            if (
                diff.parent_version_id != parent_ref.version_id
                or diff.target_version_id != target_ref.version_id
                or diff.content_digest != str(row["diff_digest"])
            ):
                self._mark_corrupt("canonical diff binding is inconsistent")
            return diff

    def put_report_snapshot(self, snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotRef:
        with self._lock, self.transaction():
            self.get_build(snapshot.build_ref)
            return self.report_snapshots.put_report_snapshot(snapshot)

    def get_report_snapshot(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshot:
        with self._lock:
            return self.report_snapshots.get_report_snapshot(ref)

    def get_report_manifest(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshotManifest:
        with self._lock:
            return self.report_snapshots.get_report_manifest(ref)

    def put_changelog(self, document: ChangeLogDocument) -> ChangeLogDocumentRef:
        self._require_project(document.version_ref.project_id)
        with self._lock, self.transaction():
            self.get_version(document.version_ref)
            return self.changelogs.put(
                document,
                version_key=_version_key(document.version_ref.variant_id, document.version_ref.version_id),
            )

    def get_changelog(self, ref: ChangeLogDocumentRef) -> ChangeLogDocument:
        with self._lock:
            return self.changelogs.get(ref)

    def put_artifact(self, entry: ArtifactLedgerEntry) -> ArtifactLedgerEntry:
        with self._lock, self.transaction():
            return self.artifacts.put(entry)

    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None:
        with self._lock:
            return self.artifacts.get(artifact_id)

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry:
        with self._lock, self.transaction():
            return self.artifacts.update(
                entry,
                expected_status=expected_status,
                expected_revision=expected_revision,
            )

    def list_evidence(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[BilingualEvidence]:
        self._require_build_ref(ref)
        return self._page_build("build_evidence", ref, request, BilingualEvidence)

    def list_candidates(self, ref: BuildResultRef, request: PageRequest = PageRequest()) -> Page[TermCandidate]:
        self._require_build_ref(ref)
        return self._page_build("build_candidates", ref, request, TermCandidate)

    def list_conflicts(
        self, ref: BuildResultRef, request: PageRequest = PageRequest(), *, filters: ConflictFilter = ConflictFilter()
    ) -> Page[ConflictGroup]:
        self._require_build_ref(ref)
        with self._lock:
            return conflict_page(self._connection, ref, request, filters)

    def list_terms(self, ref: TerminologyVersionRef, request: PageRequest = PageRequest()) -> Page[TermDecision]:
        self._require_version_ref(ref)
        with self._lock:
            return keyset_page(
                self._connection,
                table="version_terms",
                owner_column="version_key",
                owner_value=_version_key(ref.variant_id, ref.version_id),
                snapshot_digest=ref.content_digest,
                request=request,
                decode=lambda payload: self._decode(payload, TermDecision),
            )

    def list_manual_actions(self, ref: DraftRef, request: PageRequest = PageRequest()) -> Page[ManualAction]:
        with self._lock:
            return self._drafts.list_actions(ref, request)

    def list_draft_terms(self, ref: DraftRef, request: PageRequest = PageRequest()) -> Page[TermDecision]:
        with self._lock:
            return self._drafts.list_terms(ref, request)

    def list_report_terms(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[TermDecision]:
        with self._lock:
            return self.report_snapshots.list_report_terms(ref, request)

    def list_report_conflicts(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ConflictGroup]:
        with self._lock:
            return self.report_snapshots.list_report_conflicts(ref, request)

    def list_report_manual(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ManualAction]:
        with self._lock:
            return self.report_snapshots.list_report_manual(ref, request)

    def list_versions(
        self,
        project_id: str,
        variant_id: str,
        request: PageRequest = PageRequest(),
    ) -> Page[TerminologyVersionRef]:
        self._require_project(project_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT version_id, content_digest FROM versions "
                "WHERE project_id = ? AND variant_id = ? ORDER BY version_id",
                (project_id, variant_id),
            ).fetchall()
            refs = tuple(
                TerminologyVersionRef(
                    version_id=str(row["version_id"]),
                    project_id=project_id,
                    variant_id=variant_id,
                    content_digest=str(row["content_digest"]),
                )
                for row in rows
            )
            snapshot = canonical_digest(refs, namespace="terminology.version-list.v1")
            cursor = request.cursor
            if cursor is not None and (
                cursor.snapshot_digest != snapshot or cursor.query_fingerprint != request.query_fingerprint
            ):
                raise CursorStaleError("cursor does not belong to this snapshot and query")
            if cursor is not None and cursor.sort_values != (cursor.stable_id,):
                raise CursorStaleError("cursor sort key does not match the stable keyset order")
            after = None if cursor is None else cursor.stable_id
            eligible = tuple(ref for ref in refs if after is None or ref.version_id > after)
            items = eligible[: request.limit]
            next_cursor = None
            if len(eligible) > len(items) and items:
                last = items[-1].version_id
                next_cursor = SnapshotCursor(snapshot, request.query_fingerprint, (last,), last)
            return Page(items, snapshot, next_cursor, len(refs))

    def _page_build(self, table: str, ref: BuildResultRef, request: PageRequest, expected_type: type[Any]) -> Page[Any]:
        with self._lock:
            return keyset_page(
                self._connection,
                table=table,
                owner_column="build_key",
                owner_value=ref.build_key,
                snapshot_digest=ref.content_digest,
                request=request,
                decode=lambda payload: self._decode(payload, expected_type),
            )

    def _require_build_ref(self, ref: BuildResultRef) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT content_digest, project_id FROM builds WHERE build_key = ?",
                (ref.build_key,),
            ).fetchone()
            if row is None or str(row["content_digest"]) != ref.content_digest:
                raise TerminologyNotFoundError("build result was not found")
            self._require_project(str(row["project_id"]))

    def _require_version_ref(self, ref: TerminologyVersionRef) -> None:
        self._require_project(ref.project_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT content_digest, project_id FROM versions WHERE version_key = ?",
                (_version_key(ref.variant_id, ref.version_id),),
            ).fetchone()
            if (
                row is None
                or str(row["content_digest"]) != ref.content_digest
                or str(row["project_id"]) != ref.project_id
            ):
                raise TerminologyNotFoundError("terminology version was not found")

    def _insert_children(
        self,
        table: str,
        owner: str,
        items: tuple[Any, ...],
        id_attribute: str,
        *,
        owner_column: str = "build_key",
    ) -> None:
        self._connection.executemany(
            f"INSERT INTO {table}({owner_column}, stable_id, payload_json) VALUES (?, ?, ?)",
            ((owner, getattr(item, id_attribute), dumps(item)) for item in items),
        )

    def _effective_ref(self, project_id: str, variant_id: str) -> TerminologyVersionRef | None:
        row = self._connection.execute(
            "SELECT v.version_id, v.content_digest FROM effective_versions e "
            "JOIN versions v ON v.version_key = e.version_key "
            "WHERE e.project_id = ? AND e.variant_id = ?",
            (project_id, variant_id),
        ).fetchone()
        return (
            None
            if row is None
            else TerminologyVersionRef(
                version_id=str(row["version_id"]),
                project_id=project_id,
                variant_id=variant_id,
                content_digest=str(row["content_digest"]),
            )
        )

    def _decode(self, payload: str, expected_type: type[Any]) -> Any:
        try:
            return loads(payload, expected_type)
        except ValueError as exc:
            self._mark_corrupt("invalid canonical payload", cause=exc)

    def _mark_corrupt(self, diagnostic: str, *, cause: BaseException | None = None) -> None:
        self.storage_state = replace(
            self.storage_state,
            mode=StorageMode.READ_ONLY,
            integrity_ok=False,
            diagnostic=diagnostic,
        )
        self._connection.execute("PRAGMA query_only = ON")
        error = TerminologyStorageError("terminology payload is corrupt", self.storage_state)
        if cause is None:
            raise error
        raise error from cause

    def _ensure_writable(self) -> None:
        if self.storage_state.mode is StorageMode.READ_ONLY:
            raise TerminologyStorageReadOnlyError("terminology repository is read-only", self.storage_state)

    def _require_project(self, project_id: str) -> None:
        if project_id != self.project_id:
            raise RepositoryConflictError("terminology object belongs to another Project database")


def _version_key(variant_id: str, version_id: str) -> str:
    return f"{len(variant_id)}:{variant_id}{version_id}"


__all__ = ["SqliteTerminologyRepository", "SqliteTerminologyTransaction"]
