"""Repository-backed transaction stores for production lifecycle composition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from threading import RLock
from typing import Any

from transbridge.application.projects import LifecycleActivation, LifecycleSave, LifecycleSnapshot
from transbridge.application.sessions import SessionSnapshot

from .filesystem import PersistenceFilesystemPort
from .ids import ProjectId, ProjectRef, SessionRef
from .repository import ProjectRepository, VariantRepository


@dataclass(slots=True)
class _ProjectTransaction:
    mutation: LifecycleSave | LifecycleActivation | LifecycleSnapshot | None = None


class ProjectLifecycleTransactionStore:
    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
        variants: VariantRepository,
    ) -> None:
        self._documents = _AtomicDocuments(root, filesystem)
        self._projects = projects
        self._variants = variants
        self._transactions: dict[str, _ProjectTransaction] = {}
        self._lock = RLock()

    def begin(self, transaction_id: str) -> None:
        with self._lock:
            if transaction_id in self._transactions:
                raise RuntimeError("duplicate lifecycle transaction")
            self._transactions[transaction_id] = _ProjectTransaction()

    def stage_save(self, transaction_id: str, save: LifecycleSave) -> None:
        self._stage(transaction_id, save)

    def stage_activate(self, transaction_id: str, activation: LifecycleActivation) -> None:
        self._stage(transaction_id, activation)

    def stage_snapshot(self, transaction_id: str, snapshot: LifecycleSnapshot) -> None:
        self._stage(transaction_id, snapshot)

    def commit(self, transaction_id: str) -> None:
        with self._lock:
            transaction = self._required(transaction_id)
            mutation = transaction.mutation
            if mutation is None:
                raise RuntimeError("cannot commit an empty lifecycle transaction")
            if isinstance(mutation, LifecycleSave):
                project_ref = ProjectRef(ProjectId(mutation.project.envelope.identity))
                self._projects.save(project_ref, mutation.project)
                if mutation.variant is not None and mutation.formal_variant_ref is not None:
                    self._variants.save(mutation.formal_variant_ref, mutation.variant.to_dto())
            elif isinstance(mutation, LifecycleActivation):
                if mutation.candidate_project is not None:
                    project_ref = ProjectRef(ProjectId(mutation.candidate_project.envelope.identity))
                    self._projects.save(project_ref, mutation.candidate_project)
                if mutation.candidate_variant is not None and mutation.candidate_variant_ref is not None:
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
        self._documents = _AtomicDocuments(root, filesystem)
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


class _AtomicDocuments:
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        if not os.path.isabs(root):
            raise ValueError("lifecycle persistence root must be absolute")
        self._filesystem = filesystem
        self._root = filesystem.canonicalize(root)

    def write_json(self, relative_path: str, document: dict[str, Any], token: str) -> None:
        destination = self._guard(os.path.join(self._root, relative_path))
        suffix = hashlib.sha256(token.encode()).hexdigest()
        stage = self._guard(os.path.join(self._root, ".staging", f"lifecycle-{suffix}.tmp"))
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self._filesystem.make_dirs(os.path.dirname(destination))
        self._filesystem.make_dirs(os.path.dirname(stage))
        self._filesystem.remove(stage, missing_ok=True)
        try:
            self._filesystem.write_bytes(stage, payload)
            if self._filesystem.read_bytes(stage) != payload:
                raise OSError("lifecycle staging verification failed")
            self._filesystem.replace(stage, destination)
        except Exception:
            self._filesystem.remove(stage, missing_ok=True)
            raise

    def _guard(self, path: str) -> str:
        canonical = self._filesystem.canonicalize(path)
        try:
            common = os.path.commonpath((self._root, canonical))
        except ValueError as exc:
            raise ValueError("lifecycle path is on a different root") from exc
        if os.path.normcase(common) != os.path.normcase(self._root):
            raise ValueError("lifecycle path escapes persistence root")
        return canonical


__all__ = ["ProjectLifecycleTransactionStore", "SessionLifecycleTransactionStore"]
