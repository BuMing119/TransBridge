"""Single-transaction persistence for immutable terminology publication."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from typing import TYPE_CHECKING

from transbridge.application.terminology.errors import (
    DigestCollisionError,
    RepositoryConflictError,
    TerminologyNotFoundError,
)
from transbridge.application.terminology.models import (
    ArtifactLedgerEntry,
    ArtifactStatus,
    BuildResult,
    BuildResultRef,
    TerminologyDraft,
    TerminologyVersion,
    TerminologyVersionRef,
)
from transbridge.application.terminology.publish import AtomicPublishBundle

from .artifacts import ArtifactLedger
from .changelog import ChangelogDocumentStore
from .codec import dumps, loads

if TYPE_CHECKING:
    from .repository import SqliteTerminologyRepository


class SqlitePublishRepository:
    """S08 adapter that keeps publication responsibility out of the base repository."""

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self._repository = repository
        self._store = AtomicPublishStore(repository._connection, repository.project_id)

    def get_build(self, ref: BuildResultRef) -> BuildResult:
        return self._repository.get_build(ref)

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        return self._repository.active_draft(project_id, variant_id)

    def effective_version(self, project_id: str, variant_id: str) -> TerminologyVersion | None:
        return self._repository.effective_version(project_id, variant_id)

    def get_version(self, ref: TerminologyVersionRef) -> TerminologyVersion:
        return self._repository.get_version(ref)

    def publish_version_atomically(
        self,
        bundle: AtomicPublishBundle,
        *,
        business_guard: Callable[[], bool],
        fault_injector: Callable[[str], None] | None = None,
    ) -> TerminologyVersionRef:
        with self._repository._lock, self._repository.transaction():
            self._store.publish(bundle, business_guard=business_guard, fault_injector=fault_injector)
        return bundle.version.ref

    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None:
        with self._repository._lock:
            return self._repository.artifacts.get(artifact_id)

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry:
        with self._repository._lock, self._repository.transaction():
            return self._repository.artifacts.update(
                entry,
                expected_status=expected_status,
                expected_revision=expected_revision,
            )


class AtomicPublishStore:
    """Persist every authoritative publication fact before moving the pointer."""

    def __init__(self, connection: sqlite3.Connection, project_id: str) -> None:
        self._connection = connection
        self._project_id = project_id
        self._artifacts = ArtifactLedger(connection)
        self._changelogs = ChangelogDocumentStore(connection)

    def publish(
        self,
        bundle: AtomicPublishBundle,
        *,
        business_guard: Callable[[], bool],
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        version = bundle.version
        if version.ref.project_id != self._project_id:
            raise RepositoryConflictError("terminology version belongs to another Project database")
        if not business_guard():
            raise RepositoryConflictError("terminology expected state or run permit is stale")
        self._fault(fault_injector, "guard_validated")
        self._validate_build(bundle)
        effective_id = self._effective_version_id(version.ref.variant_id)
        if effective_id != bundle.expected.effective_version_id or version.parent_version_id != effective_id:
            raise RepositoryConflictError("published version parent must be the current effective version")
        self._validate_draft(bundle, effective_id)
        self._fault(fault_injector, "inputs_validated")

        version_key = version_key_for(version.ref.variant_id, version.ref.version_id)
        existing = self._connection.execute(
            "SELECT payload_json FROM versions WHERE version_key = ?",
            (version_key,),
        ).fetchone()
        if existing is not None:
            if loads(str(existing["payload_json"]), TerminologyVersion) != version:
                raise DigestCollisionError(version.ref.version_id)
            raise RepositoryConflictError("terminology version has already been published")
        parent_key = (
            None
            if version.parent_version_id is None
            else version_key_for(version.ref.variant_id, version.parent_version_id)
        )
        self._connection.execute(
            "INSERT INTO versions(version_key, version_id, project_id, variant_id, content_digest, "
            "parent_version_key, build_key, published_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_key,
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
        self._children("version_terms", version_key, version.decisions, "term_id")
        self._children("version_conflicts", version_key, version.conflicts, "conflict_group_id")
        self._children("version_manual_actions", version_key, version.manual_actions, "action_id")
        self._fault(fault_injector, "version_membership_written")

        self._connection.execute(
            "INSERT INTO canonical_diffs(version_key, content_digest, payload_json) VALUES (?, ?, ?)",
            (version_key, version.canonical_diff.content_digest, dumps(version.canonical_diff)),
        )
        self._fault(fault_injector, "canonical_diff_written")
        changelog = bundle.changelog
        if changelog.version_ref != version.ref or changelog.ref != version.changelog_ref:
            raise RepositoryConflictError("changelog must describe the published version")
        self._changelogs.put(changelog, version_key=version_key)
        self._fault(fault_injector, "changelog_written")
        for artifact in bundle.artifacts:
            if artifact.owner_ref != changelog.ref.document_id:
                raise RepositoryConflictError("artifact owner must be the frozen changelog document")
            self._artifacts.put(artifact)
        self._fault(fault_injector, "artifact_ledger_written")

        if bundle.expected_draft_ref is not None:
            self._connection.execute(
                "DELETE FROM drafts WHERE project_id = ? AND variant_id = ? AND draft_id = ? AND revision = ?",
                (
                    version.ref.project_id,
                    version.ref.variant_id,
                    bundle.expected_draft_ref.draft_id,
                    bundle.expected_draft_ref.revision,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RepositoryConflictError("reviewed draft changed during publication")
        self._fault(fault_injector, "draft_consumed")
        self._connection.execute(
            "INSERT INTO effective_versions(project_id, variant_id, version_key) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id, variant_id) DO UPDATE SET version_key=excluded.version_key",
            (version.ref.project_id, version.ref.variant_id, version_key),
        )
        self._fault(fault_injector, "effective_pointer_moved")

    def _validate_build(self, bundle: AtomicPublishBundle) -> None:
        ref = bundle.version.build_ref
        row = self._connection.execute(
            "SELECT payload_json FROM builds WHERE build_key = ? AND content_digest = ?",
            (ref.build_key, ref.content_digest),
        ).fetchone()
        if row is None:
            raise TerminologyNotFoundError("build result was not found")
        build = loads(str(row["payload_json"]), BuildResult).require_publishable()
        if (build.project_id, build.variant_id) != (
            bundle.version.ref.project_id,
            bundle.version.ref.variant_id,
        ):
            raise RepositoryConflictError("build belongs to another Project/Variant")

    def _validate_draft(self, bundle: AtomicPublishBundle, effective_id: str | None) -> None:
        version = bundle.version
        row = self._connection.execute(
            "SELECT payload_json FROM drafts WHERE project_id = ? AND variant_id = ?",
            (version.ref.project_id, version.ref.variant_id),
        ).fetchone()
        expected_ref = bundle.expected_draft_ref
        if expected_ref is None:
            if row is not None or bundle.expected.draft_id != "no-draft":
                raise RepositoryConflictError("rollback requires the no-draft line")
            return
        if row is None:
            raise RepositoryConflictError("reviewed draft is absent")
        draft = loads(str(row["payload_json"]), TerminologyDraft)
        if draft.ref != expected_ref:
            raise RepositoryConflictError("reviewed draft changed during publication")
        if (bundle.expected.draft_id, bundle.expected.draft_revision) != (
            draft.ref.draft_id,
            draft.ref.revision,
        ):
            raise RepositoryConflictError("expected draft state does not match the reviewed draft")
        if draft.ref.base_version_id != effective_id or bundle.expected.base_version_id != effective_id:
            raise RepositoryConflictError("reviewed draft base is not the current effective version")
        if draft.decisions != version.decisions or draft.actions != version.manual_actions:
            raise RepositoryConflictError("published membership differs from the reviewed draft")

    def _effective_version_id(self, variant_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT v.version_id FROM effective_versions e "
            "JOIN versions v ON v.version_key = e.version_key "
            "WHERE e.project_id = ? AND e.variant_id = ?",
            (self._project_id, variant_id),
        ).fetchone()
        return None if row is None else str(row["version_id"])

    def _children(self, table: str, owner: str, items: tuple[object, ...], identity: str) -> None:
        self._connection.executemany(
            f"INSERT INTO {table}(version_key, stable_id, payload_json) VALUES (?, ?, ?)",
            ((owner, getattr(item, identity), dumps(item)) for item in items),
        )

    @staticmethod
    def _fault(injector: Callable[[str], None] | None, step: str) -> None:
        if injector is not None:
            injector(step)


def version_key_for(variant_id: str, version_id: str) -> str:
    return f"{len(variant_id)}:{variant_id}{version_id}"


__all__ = ["AtomicPublishStore", "SqlitePublishRepository", "version_key_for"]
