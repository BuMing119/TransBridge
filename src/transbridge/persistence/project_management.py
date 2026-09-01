"""Transactional persistence for authoritative Project deletion."""

from __future__ import annotations

from collections.abc import Callable
import os
import re

from transbridge.application.projects.management import ProjectDeletion
from transbridge.persistence.project_catalog_document import (
    build_project_catalog,
    parse_project_catalog,
    project_display_name,
)

from .v2.atomic_documents import AtomicDocumentStore
from .v2.filesystem import PersistenceFilesystemPort, RepositoryPaths
from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.repository import ProjectRepository, ProjectRevisionConflict, VariantRepository
from .v2.schema import parse_json_bytes


class ProjectManagementStore:
    """Delete only records whose paths are derived from one opaque Project ID."""

    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
        variants: VariantRepository,
        *,
        token_factory: Callable[[], str],
    ) -> None:
        self._filesystem = filesystem
        self._projects = projects
        self._variants = variants
        self._paths = RepositoryPaths(root, filesystem)
        self._documents = AtomicDocumentStore(root, filesystem)
        self._token_factory = token_factory
        self._catalog_path = self._documents.path("project-catalog.json")
        self._active_path = self._documents.path("active-project.json")
        self._snapshot_directory = self._documents.path("snapshots")

    def delete(
        self,
        ref: ProjectRef,
        *,
        expected_revision: int | None = None,
        expected_name: str | None = None,
    ) -> ProjectDeletion:
        with self._projects.mutation_lock:
            project = self._projects.read_snapshot(ref)
            if expected_revision is not None and project.envelope.revision != expected_revision:
                raise ProjectRevisionConflict(expected_revision, project.envelope.revision)
            name = project_display_name(project.envelope.data.get("name"))
            if expected_name is not None and project_display_name(expected_name) != name:
                raise ValueError("Project name changed after deletion was confirmed")
            variant_ids = self._variant_ids(project.envelope.data.get("variant_ids"))
            for variant_id in variant_ids:
                self._variants.read_snapshot(VariantRef(VariantId(variant_id), ref.identity))

            catalog_raw = self._required_bytes(self._catalog_path, "Project catalog is missing")
            records = parse_project_catalog(catalog_raw)
            record = next((item for item in records if item.project_id == ref.identity.value), None)
            if record is None or record.name != name:
                raise ValueError("Project catalog does not match the authoritative Project")
            remaining = tuple(item for item in records if item.project_id != ref.identity.value)

            active_raw = (
                self._filesystem.read_bytes(self._active_path) if self._filesystem.exists(self._active_path) else None
            )
            was_active = self._is_active(active_raw, ref.identity)
            snapshot_paths = self._owned_snapshot_paths(ref, frozenset(variant_ids))
            owned_directory = self._paths.project_data(ref)
            backup_directory = self._paths.project_backup_data(ref)
            owned_paths = self._filesystem.list_tree_files(owned_directory)
            backup_paths = self._filesystem.list_tree_files(backup_directory)
            project_path = self._projects.path_for(ref)
            deletion_paths = tuple(dict.fromkeys((*snapshot_paths, *owned_paths, *backup_paths, project_path)))
            preimages = {
                path: self._required_bytes(path, "Project-owned record disappeared") for path in deletion_paths
            }

            token = self._token_factory()
            removed: list[str] = []
            catalog_published = False
            active_published = False
            try:
                # Remove discovery first. A partial physical delete can therefore
                # never leave a valid catalog entry pointing at a missing Project.
                self._documents.write_json(
                    "project-catalog.json",
                    build_project_catalog(remaining),
                    f"{token}-catalog",
                    durable=True,
                )
                catalog_published = True
                if was_active:
                    self._documents.write_json(
                        "active-project.json",
                        {
                            "schema_version": 1,
                            "project_id": None,
                            "variant_id": None,
                            "source_ref": None,
                        },
                        f"{token}-active",
                        durable=True,
                    )
                    active_published = True
                for path in deletion_paths:
                    self._filesystem.remove(self._paths.guard(path), missing_ok=False)
                    removed.append(path)
                self._filesystem.remove_empty_tree(owned_directory)
                self._filesystem.remove_empty_tree(backup_directory)
            except Exception:
                restored = self._restore_removed(preimages, tuple(removed), token)
                if restored:
                    if active_published:
                        self._restore(self._active_path, active_raw, f"{token}-restore-active")
                    if catalog_published:
                        self._restore(self._catalog_path, catalog_raw, f"{token}-restore-catalog")
                raise
            return ProjectDeletion(ref.identity.value, name, variant_ids, was_active)

    def _owned_snapshot_paths(self, ref: ProjectRef, variant_ids: frozenset[str]) -> tuple[str, ...]:
        paths: list[str] = []
        for path in self._filesystem.list_files(self._snapshot_directory):
            guarded = self._paths.guard(path)
            identity, extension = os.path.splitext(os.path.basename(guarded))
            if extension != ".json" or not re.fullmatch(r"[0-9a-f]{64}", identity):
                continue
            document = parse_json_bytes(self._filesystem.read_bytes(guarded))
            if document.get("project_id") != ref.identity.value:
                continue
            variant = document.get("variant")
            if not isinstance(variant, dict) or variant.get("id") not in variant_ids:
                raise ValueError("Project snapshot ownership does not match its Project")
            paths.append(guarded)
        return tuple(paths)

    @staticmethod
    def _variant_ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError("Project Variant catalog is invalid")
        identities = tuple(VariantId(item).value for item in value if isinstance(item, str))
        if len(identities) != len(value) or len(set(identities)) != len(identities):
            raise ValueError("Project Variant catalog is invalid")
        return identities

    @staticmethod
    def _is_active(raw: bytes | None, project_id: ProjectId) -> bool:
        if raw is None:
            return False
        document = parse_json_bytes(raw)
        if document.get("schema_version") != 1:
            raise ValueError("Active Project pointer is invalid")
        value = document.get("project_id")
        if value is not None:
            ProjectId(value)
        return value == project_id.value

    def _required_bytes(self, path: str, message: str) -> bytes:
        if not self._filesystem.exists(path):
            raise FileNotFoundError(message)
        return self._filesystem.read_bytes(path)

    def _restore_removed(self, preimages: dict[str, bytes], removed: tuple[str, ...], token: str) -> bool:
        try:
            for index, path in enumerate(reversed(removed)):
                self._documents.write_bytes(path, preimages[path], f"{token}-restore-{index}", durable=True)
            return all(self._filesystem.read_bytes(path) == preimages[path] for path in removed)
        except Exception:
            return False

    def _restore(self, path: str, raw: bytes | None, token: str) -> None:
        if raw is None:
            self._filesystem.remove(path, missing_ok=True)
        else:
            self._documents.write_bytes(path, raw, token, durable=True)


__all__ = ["ProjectDeletion", "ProjectManagementStore"]
