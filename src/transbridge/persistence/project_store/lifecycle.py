# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Publish the normal application lifecycle in current project transactions."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
import logging
import sqlite3
from threading import RLock

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.projects.models import LifecycleActivation, LifecycleProjectUpdate, LifecycleSave
from transbridge.application.projects.provisioning_models import ProjectProvisioningCommit
from transbridge.application.projects.save_changes import project_needs_encoding_defaults
from transbridge.persistence.terminology.project_selection import decode_selection
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId
from transbridge.persistence.v2.models import PathBoundaryError, ProjectDto

from .catalog import ActiveProjectPreference, ProjectCatalog
from .connection import project_connection
from .entry_codec import encode_json, pack_text
from .format import ProjectStorageConflict, ProjectStoreError
from .history import write_snapshot
from .reads import require_variant
from .receipts import record_applications
from .records import WorkspaceProjects, WorkspaceVariants, project_settings
from .repository import publish_project
from .save_verification import save_matches
from .variant_rows import save_sources, write_variant
from .writes import insert_project

logger = logging.getLogger(__name__)


class ProjectLifecycleStore:
    def __init__(
        self,
        projects: WorkspaceProjects,
        variants: WorkspaceVariants,
        catalog: ProjectCatalog,
        active: ActiveProjectPreference,
        baselines,
    ) -> None:
        self.projects = projects
        self.variants = variants
        self.catalog = catalog
        self.active = active
        self.baselines = baselines
        self._transactions = {}
        self._lock = RLock()

    def begin(self, transaction_id: str) -> None:
        with self._lock:
            if transaction_id in self._transactions:
                raise RuntimeError("duplicate project transaction")
            self._transactions[transaction_id] = None

    def _stage(self, transaction_id: str, mutation) -> None:
        with self._lock:
            if self._transactions[transaction_id] is not None:
                raise RuntimeError("project transaction already staged")
            self._transactions[transaction_id] = mutation

    stage_save = _stage
    stage_project_update = _stage
    stage_activate = _stage
    stage_snapshot = _stage
    stage_provisioning = _stage

    def rollback(self, transaction_id: str) -> None:
        with self._lock:
            self._transactions.pop(transaction_id, None)

    def project_exists(self, ref) -> bool:
        return self.projects.exists(ref)

    def variant_exists(self, ref) -> bool:
        return self.variants.exists(ref)

    def project_name_exists(self, name_key: str) -> bool:
        return any(item.name.casefold() == name_key for item in self.catalog.list_projects().projects)

    def verify_saved(self, save: LifecycleSave) -> bool:
        path = self.projects.store_for(ProjectId(save.project.envelope.identity)).path
        with project_connection(path) as connection:
            return save_matches(connection, save)

    def commit(self, transaction_id: str) -> tuple[Diagnostic, ...]:
        with self._lock:
            mutation = self._transactions[transaction_id]
            preference_warning = None
            if isinstance(mutation, ProjectProvisioningCommit):
                preference_warning = self._provision(mutation, transaction_id)
            elif isinstance(mutation, LifecycleSave):
                self._save(mutation)
            elif isinstance(mutation, LifecycleProjectUpdate):
                self._update(mutation)
            elif isinstance(mutation, LifecycleActivation):
                preference_warning = self._activate(mutation, transaction_id)
            elif mutation is not None:
                with project_connection(
                    self.projects.store_for(mutation.project_ref.identity).path, write=True
                ) as connection:
                    write_snapshot(connection, mutation, transaction_id)
            else:
                raise RuntimeError("project transaction has no staged mutation")
            self._transactions.pop(transaction_id)
            return () if preference_warning is None else (preference_warning,)

    def _connection(self, project: ProjectDto):
        return project_connection(self.projects.store_for(ProjectId(project.envelope.identity)).path, write=True)

    def _provision(self, commit: ProjectProvisioningCommit, token: str) -> Diagnostic | None:
        data = commit.project.envelope.data
        if len(commit.baselines) != len(commit.extracted_sources):
            raise ProjectStoreError("project provisioning requires translation data for every source")
        project_id = commit.project_ref.identity
        variant_id = commit.variant_ref.identity

        def populate(connection: sqlite3.Connection) -> None:
            insert_project(
                connection,
                project_id,
                data["name"],
                variant_id,
                data["variant_names"][variant_id.value],
                commit.extracted_sources,
            )
            connection.execute("UPDATE project SET settings = ?", (project_settings(commit.project),))

        publish_project(
            self.projects.store_for(project_id).path,
            populate,
            publication=self.catalog.publish_name(data["name"]),
        )
        self.baselines.register(commit.project_ref, commit.variant_ref, commit.baselines, allow_empty=True)
        return self._publish_pointer(project_id, token)

    def _update(self, update: LifecycleProjectUpdate) -> None:
        project = update.project
        project_id = ProjectId(project.envelope.identity)
        current = self.projects.store_for(project_id).summary()
        name = project.envelope.data["name"]
        publication = nullcontext() if name == current.name else self.catalog.publish_name(name, excluding=project_id)
        with publication, self._connection(project) as connection:
            _verify_project(connection, project, update.expected_persisted_project_revision)
            _write_project(connection, project)

    def _save(self, save: LifecycleSave) -> None:
        with self._connection(save.project) as connection:
            _verify_project(connection, save.project, save.expected_persisted_project_revision)
            snapshot = save.variant
            project_changed = save.project.envelope.revision != save.expected_persisted_project_revision
            if project_changed or save.source_additions:
                save_sources(connection, save)
            if project_changed or project_needs_encoding_defaults(save.project):
                _write_project(connection, save.project)
            if snapshot is not None and snapshot.revision != save.expected_persisted_revision:
                changes = snapshot.entries if save.entry_changes is None else save.entry_changes
                write_variant(connection, snapshot, changes, save.expected_persisted_revision)
            record_applications(connection, save)

    def _activate(self, activation: LifecycleActivation, token: str) -> Diagnostic | None:
        project = activation.candidate_project
        if project is None:
            self.active.set(None, token)
            return None
        project_id = ProjectId(project.envelope.identity)
        with self._connection(project) as connection:
            _verify_project(connection, project, activation.expected_project_revision)
            variant = activation.candidate_variant
            if variant is not None:
                expected = activation.expected_variant_revision
                if expected is None:
                    name = project.envelope.data["variant_names"][variant.ref.identity.value]
                    connection.execute(
                        "INSERT INTO variant (identity, name, ordinal, revision) "
                        "VALUES (?, ?, (SELECT COALESCE(MAX(ordinal), -1) + 1 FROM variant), 0)",
                        (variant.ref.identity.value, pack_text(name)),
                    )
                    expected = 0
                if require_variant(connection, variant.ref.identity)["revision"] != expected:
                    raise ProjectStorageConflict("Variant changed before activation")
                if activation.write_candidate_variant:
                    write_variant(connection, variant, variant.entries, expected)
            for ref, revision, _marker in activation.checked_variants:
                if require_variant(connection, ref.identity)["revision"] != revision:
                    raise ProjectStorageConflict("related Variant changed before activation")
            if activation.write_candidate_project:
                _write_project(connection, project)
            for selection in activation.terminology_selections:
                variant_row = require_variant(connection, VariantId(selection.variant_id))
                current = decode_selection(
                    variant_row["terminology_selection"], selection.project_id, selection.variant_id
                )
                if selection.project_id != project_id.value or current != selection.expected:
                    raise ProjectStorageConflict("terminology selection changed before activation")
                connection.execute(
                    "UPDATE variant SET terminology_selection = ? WHERE identity = ?",
                    (None if selection.target is None else encode_json(asdict(selection.target)), selection.variant_id),
                )
            if activation.removed_variant is not None:
                ref, revision, _marker = activation.removed_variant
                cursor = connection.execute(
                    "DELETE FROM variant WHERE identity = ? AND revision = ?", (ref.identity.value, revision)
                )
                if cursor.rowcount != 1:
                    raise ProjectStorageConflict("removed Variant changed before activation")
        ref = ProjectRef(project_id)
        if activation.candidate_baselines is not None and activation.candidate_variant_ref is not None:
            self.baselines.register(
                ref, activation.candidate_variant_ref, activation.candidate_baselines, allow_empty=True
            )
        if activation.removed_variant is not None:
            self.baselines.remove(ref, activation.removed_variant[0])
        return self._publish_pointer(project_id, token)

    def _publish_pointer(self, project_id: ProjectId, token: str) -> Diagnostic | None:
        try:
            self.active.set(project_id, token)
        except (OSError, PathBoundaryError):
            logger.exception("Project data is saved, but the active project preference could not be published")
            return Diagnostic(
                "PROJECT_ACTIVE_PREFERENCE_WRITE_FAILED",
                "项目数据已保存，但无法确认下次启动时自动恢复的项目是否已更新。",
                DiagnosticSeverity.WARNING,
                ErrorCategory.EXTERNAL,
            )
        return None


def _verify_project(connection: sqlite3.Connection, project: ProjectDto, expected_revision: int | None) -> None:
    row = connection.execute("SELECT identity, revision FROM project").fetchone()
    if row["identity"] != project.envelope.identity or row["revision"] != expected_revision:
        raise ProjectStorageConflict("project changed before committing the operation")


def _write_project(connection: sqlite3.Connection, project: ProjectDto) -> None:
    data = project.envelope.data
    connection.execute(
        "UPDATE project SET name = ?, active_variant = ?, revision = ?, settings = ?",
        (pack_text(data["name"]), data["active_variant_id"], project.envelope.revision, project_settings(project)),
    )
