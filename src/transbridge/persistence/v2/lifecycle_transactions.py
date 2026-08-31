"""Repository-backed transaction stores for production lifecycle composition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from threading import RLock
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.projects import (
    LifecycleActivation,
    LifecycleProjectUpdate,
    LifecycleSave,
    LifecycleSnapshot,
    ProjectProvisioningCommit,
)
from transbridge.application.sessions import SessionSnapshot
from transbridge.persistence.project_catalog_document import build_project_catalog, parse_project_catalog

from .atomic_documents import AtomicDocumentStore
from .baselines import BaselineRegistry
from .filesystem import PersistenceFilesystemPort
from .ids import ProjectId, ProjectRef, SessionRef
from .models import SchemaValidationError
from .project_save_journal import ProjectSaveProtocol
from .repository import ProjectRepository, ProjectRevisionConflict, VariantRepository


@dataclass(slots=True)
class _ProjectTransaction:
    mutation: (
        LifecycleSave
        | LifecycleProjectUpdate
        | LifecycleActivation
        | LifecycleSnapshot
        | ProjectProvisioningCommit
        | None
    ) = None


class ProjectLifecycleTransactionStore:
    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
        variants: VariantRepository,
        baselines: BaselineRegistry | None = None,
    ) -> None:
        self._documents = AtomicDocumentStore(root, filesystem)
        self._filesystem = filesystem
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._project_save = ProjectSaveProtocol(root, filesystem, projects, variants)
        self._transactions: dict[str, _ProjectTransaction] = {}
        self._lock = RLock()

    def begin(self, transaction_id: str) -> None:
        with self._lock:
            if transaction_id in self._transactions:
                raise RuntimeError("duplicate lifecycle transaction")
            self._transactions[transaction_id] = _ProjectTransaction()

    def stage_save(self, transaction_id: str, save: LifecycleSave) -> None:
        self._stage(transaction_id, save)

    def stage_project_update(self, transaction_id: str, update: LifecycleProjectUpdate) -> None:
        self._stage(transaction_id, update)

    def stage_activate(self, transaction_id: str, activation: LifecycleActivation) -> None:
        self._stage(transaction_id, activation)

    def stage_snapshot(self, transaction_id: str, snapshot: LifecycleSnapshot) -> None:
        self._stage(transaction_id, snapshot)

    def stage_provisioning(
        self,
        transaction_id: str,
        provisioning: ProjectProvisioningCommit,
    ) -> None:
        self._stage(transaction_id, provisioning)

    def project_exists(self, ref) -> bool:
        return self._filesystem.exists(self._projects.path_for(ref))

    def variant_exists(self, ref) -> bool:
        return self._filesystem.exists(self._variants.path_for(ref))

    def project_name_exists(self, name_key: str) -> bool:
        catalog = self._read_project_catalog()
        return any(item.get("name_key") == name_key for item in catalog["projects"].values())

    def commit(self, transaction_id: str) -> None:
        with self._lock:
            # No lifecycle mutation may advance Project/Variant state while a
            # prior two-document save still owns the recovery boundary.
            self._project_save.recover_pending()
            transaction = self._required(transaction_id)
            mutation = transaction.mutation
            if mutation is None:
                raise RuntimeError("cannot commit an empty lifecycle transaction")
            if isinstance(mutation, ProjectProvisioningCommit):
                self._commit_provisioning(transaction_id, mutation)
            elif isinstance(mutation, LifecycleSave):
                self._project_save.commit(mutation, transaction_id)
            elif isinstance(mutation, LifecycleProjectUpdate):
                project_ref = ProjectRef(ProjectId(mutation.project.envelope.identity))
                try:
                    self._projects.save_if_revision(
                        project_ref,
                        mutation.project,
                        expected_revision=mutation.expected_persisted_project_revision,
                    )
                except ProjectRevisionConflict as exc:
                    raise DomainError(
                        ErrorCategory.CONFLICT,
                        "PROJECT_UPDATE_PERSISTED_STALE",
                        "The persisted Project changed before the update could commit.",
                        details={
                            "expected_revision": exc.expected_revision,
                            "actual_revision": exc.actual_revision,
                        },
                        cause=exc,
                    ) from exc
            elif isinstance(mutation, LifecycleActivation):
                if mutation.candidate_project is not None and mutation.write_candidate_project:
                    project_ref = ProjectRef(ProjectId(mutation.candidate_project.envelope.identity))
                    self._projects.save(project_ref, mutation.candidate_project)
                if (
                    mutation.candidate_variant is not None
                    and mutation.candidate_variant_ref is not None
                    and mutation.write_candidate_variant
                ):
                    self._variants.save(
                        mutation.candidate_variant_ref,
                        mutation.candidate_variant.to_dto(),
                    )
                self._documents.write_json(
                    "active-project.json",
                    {
                        "schema_version": 1,
                        "project_id": (
                            None if mutation.candidate_project is None else mutation.candidate_project.envelope.identity
                        ),
                        "variant_id": (
                            None
                            if mutation.candidate_variant_ref is None
                            else mutation.candidate_variant_ref.identity.value
                        ),
                        "source_ref": mutation.source_ref,
                    },
                    transaction_id,
                )
            else:
                digest = hashlib.sha256(
                    f"{mutation.project_ref.identity.value}:{mutation.formal_variant_ref.identity.value}:"
                    f"{mutation.variant.revision}:{mutation.name}".encode()
                ).hexdigest()
                self._documents.write_json(
                    os.path.join("snapshots", f"{digest}.json"),
                    {
                        "schema_version": 1,
                        "name": mutation.name,
                        "project_id": mutation.project_ref.identity.value,
                        "variant": mutation.variant.to_dto().envelope.to_dict(),
                    },
                    transaction_id,
                )
            self._transactions.pop(transaction_id)

    def rollback(self, transaction_id: str) -> None:
        with self._lock:
            self._transactions.pop(transaction_id, None)

    def _stage(self, transaction_id: str, mutation: Any) -> None:
        with self._lock:
            transaction = self._required(transaction_id)
            if transaction.mutation is not None:
                raise RuntimeError("lifecycle transaction already has a mutation")
            transaction.mutation = mutation

    def _commit_provisioning(
        self,
        transaction_id: str,
        provisioning: ProjectProvisioningCommit,
    ) -> None:
        project_ref = provisioning.project_ref
        variant_ref = provisioning.variant_ref
        if self.project_exists(project_ref) or self.variant_exists(variant_ref):
            raise RuntimeError("provisioning identity already exists")
        catalog = self._read_project_catalog()
        if any(item.get("name_key") == provisioning.project_name_key for item in catalog["projects"].values()):
            raise RuntimeError("provisioning Project name already exists")

        catalog_path = self._documents.path("project-catalog.json")
        previous_catalog = self._filesystem.read_bytes(catalog_path) if self._filesystem.exists(catalog_path) else None
        project_created = False
        variant_created = False
        baseline_registered = False
        try:
            self._projects.save(project_ref, provisioning.project)
            project_created = True
            self._variants.save(variant_ref, provisioning.variant.to_dto())
            variant_created = True
            catalog["projects"][project_ref.identity.value] = {
                "name": provisioning.project.envelope.data["name"],
                "name_key": provisioning.project_name_key,
            }
            self._documents.write_json("project-catalog.json", catalog, transaction_id)
            if self._baselines is not None:
                self._baselines.register(
                    project_ref,
                    variant_ref,
                    provisioning.baselines,
                    allow_empty=not provisioning.baselines,
                )
                baseline_registered = True
            # The active pointer is the publication boundary and is always last.
            self._documents.write_json(
                "active-project.json",
                {
                    "schema_version": 1,
                    "project_id": project_ref.identity.value,
                    "variant_id": variant_ref.identity.value,
                    "source_ref": None,
                },
                transaction_id,
            )
        except Exception:
            if baseline_registered and self._baselines is not None:
                self._baselines.remove(project_ref, variant_ref)
            self._restore_document(catalog_path, previous_catalog, transaction_id)
            if variant_created:
                try:
                    self._variants.delete(variant_ref)
                except Exception:
                    pass
            if project_created:
                try:
                    self._projects.delete(project_ref)
                except Exception:
                    pass
            raise

    def _read_project_catalog(self) -> dict[str, Any]:
        path = self._documents.path("project-catalog.json")
        if not self._filesystem.exists(path):
            return build_project_catalog(())
        try:
            records = parse_project_catalog(self._filesystem.read_bytes(path))
        except (OSError, SchemaValidationError, TypeError, ValueError) as exc:
            raise RuntimeError("Project catalog is invalid") from exc
        return build_project_catalog(records)

    def _restore_document(self, path: str, previous: bytes | None, token: str) -> None:
        try:
            if previous is None:
                self._filesystem.remove(path, missing_ok=True)
            else:
                self._documents.write_bytes(path, previous, f"{token}-restore")
        except Exception:
            # Cleanup failure cannot replace the original transaction failure.
            return

    def _required(self, transaction_id: str) -> _ProjectTransaction:
        try:
            return self._transactions[transaction_id]
        except KeyError as exc:
            raise RuntimeError("unknown lifecycle transaction") from exc


@dataclass(slots=True)
class _SessionTransaction:
    old: SessionRef | None = None
    candidate: SessionSnapshot | None = None
    staged: bool = False


class SessionLifecycleTransactionStore:
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        self._documents = AtomicDocumentStore(root, filesystem)
        self._transactions: dict[str, _SessionTransaction] = {}
        self._lock = RLock()

    def begin(self, transaction_id: str) -> None:
        with self._lock:
            if transaction_id in self._transactions:
                raise RuntimeError("duplicate Session transaction")
            self._transactions[transaction_id] = _SessionTransaction()

    def stage_activate(
        self,
        transaction_id: str,
        old: SessionRef | None,
        candidate: SessionSnapshot | None,
    ) -> None:
        with self._lock:
            transaction = self._transactions[transaction_id]
            if transaction.staged:
                raise RuntimeError("Session transaction already has an activation")
            transaction.old = old
            transaction.candidate = candidate
            transaction.staged = True

    def commit(self, transaction_id: str) -> None:
        with self._lock:
            transaction = self._transactions[transaction_id]
            if not transaction.staged:
                raise RuntimeError("cannot commit an empty Session transaction")
            self._documents.write_json(
                "active-session.json",
                {
                    "schema_version": 1,
                    "session_id": (None if transaction.candidate is None else transaction.candidate.ref.identity.value),
                    "revision": None if transaction.candidate is None else transaction.candidate.revision,
                },
                transaction_id,
            )
            self._transactions.pop(transaction_id)

    def rollback(self, transaction_id: str) -> None:
        with self._lock:
            self._transactions.pop(transaction_id, None)


__all__ = ["ProjectLifecycleTransactionStore", "SessionLifecycleTransactionStore"]
