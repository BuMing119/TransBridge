"""Read-only workspace discovery and the new active-project preference."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, DomainError, ErrorCategory
from transbridge.application.projects.management import ProjectDeletion
from transbridge.application.projects.recent_catalog import ProjectCatalogEntry, ProjectCatalogSnapshot
from transbridge.persistence.project_catalog_document import project_name_key
from transbridge.persistence.v2.atomic_documents import AtomicDocumentStore
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import ReadOnlyWriteRefused
from transbridge.persistence.v2.session_write_lease import persistence_write_lease

from .connection import project_connection, project_mutation_lease
from .format import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_ROOT,
    ProjectStorageConflict,
    ProjectStoreError,
    UnsupportedProjectFormat,
)
from .reads import read_summary
from .records import WorkspaceProjects

_ACTIVE = f"{PROJECT_ROOT}/active.json"
logger = logging.getLogger(__name__)


class ActiveProjectPreference:
    def __init__(self, root: str, filesystem) -> None:
        self._documents = AtomicDocumentStore(root, filesystem)
        self.path = Path(self._documents.path(_ACTIVE))

    def read(self) -> ProjectId | None:
        if not self.path.is_file():
            return None
        pointer = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(pointer, dict) or pointer.get("format") != PROJECT_FORMAT:
            raise ProjectStoreError("active project preference has an unsupported format")
        return ProjectId(pointer["project_id"])

    def set(self, project_id: ProjectId | None, token: str) -> None:
        if project_id is None:
            self._documents.remove_durable(str(self.path), token)
        else:
            self._documents.write_json(
                _ACTIVE, {"format": PROJECT_FORMAT, "project_id": project_id.value}, token, durable=True
            )


class ProjectCatalog:
    def __init__(self, projects: WorkspaceProjects, active: ActiveProjectPreference) -> None:
        self.projects = projects
        self.active = active

    @contextmanager
    def publish_name(self, name: str, *, excluding: ProjectId | None = None) -> Iterator[None]:
        """Serialize the final name check and publication across workspace writers."""
        name_key = project_name_key(name)
        busy_code = "PROJECT_CATALOG_WRITER_BUSY"
        try:
            with persistence_write_lease(str(self.projects.directory), "catalog", OsPersistenceFilesystem(), busy_code):
                if any(
                    item.name.casefold() == name_key and (excluding is None or item.project_id != excluding.value)
                    for item in self.list_projects(require_readable=True).projects
                ):
                    raise DomainError(
                        ErrorCategory.CONFLICT, "PROJECT_NAME_ALREADY_EXISTS", "已有同名项目，请选择新名称。"
                    )
                yield
        except ReadOnlyWriteRefused as exc:
            if exc.code != busy_code:
                raise
            raise DomainError(
                ErrorCategory.CONFLICT, busy_code, "项目目录正在发布变更，请重试。", retryable=True, cause=exc
            ) from exc

    def list_projects(self, *, require_readable: bool = False) -> ProjectCatalogSnapshot:
        diagnostics = []
        try:
            active = self.active.read()
        except (KeyError, ValueError, OSError, ProjectStoreError) as exc:
            active = None
            logger.warning("Could not read the current project preference: %s", exc)
            diagnostics.append(_catalog_diagnostic("PROJECT_ACTIVE_PREFERENCE_INVALID", "当前项目偏好无法读取。"))
        entries = []
        for path in self.projects.directory.glob(f"id-*/{PROJECT_FILENAME}"):
            if path.resolve() != path:
                continue
            try:
                with project_connection(path) as connection:
                    summary = read_summary(connection)
            except (ValueError, ProjectStoreError) as exc:
                if (
                    require_readable
                    and isinstance(exc, ProjectStoreError)
                    and not isinstance(exc, UnsupportedProjectFormat)
                ):
                    raise
                logger.warning("Could not read project %s: %s", path.parent.name, exc)
                diagnostics.append(
                    _catalog_diagnostic(
                        "PROJECT_CATALOG_RECORD_UNAVAILABLE", f"不支持的项目格式或数据库损坏：{path.parent.name}"
                    )
                )
                continue
            if path.parent.name != summary.identity.encoded:
                diagnostics.append(
                    _catalog_diagnostic("PROJECT_CATALOG_IDENTITY_MISMATCH", "项目身份与所属目录不一致。")
                )
                continue
            entries.append(
                ProjectCatalogEntry(summary.identity.value, summary.name, str(path), summary.identity == active, True)
            )
        return ProjectCatalogSnapshot(
            tuple(sorted(entries, key=lambda entry: (entry.name.casefold(), entry.project_id))),
            tuple(diagnostics),
        )


def _catalog_diagnostic(code: str, message: str) -> Diagnostic:
    return Diagnostic(code, message, DiagnosticSeverity.WARNING, ErrorCategory.PREREQUISITE)


class ProjectDeletionStore:
    def __init__(self, projects: WorkspaceProjects, active: ActiveProjectPreference) -> None:
        self.projects = projects
        self.active = active

    def delete(
        self, ref: ProjectRef, *, expected_revision: int | None = None, expected_name: str | None = None
    ) -> ProjectDeletion:
        path = self.projects.store_for(ref.identity).path
        parent = path.parent.resolve()
        root = self.projects.directory.resolve()
        if parent.parent != root or parent.name != ref.identity.encoded:
            raise ProjectStoreError("project deletion path is outside the workspace")
        tombstone = root / f".deleted-{uuid4().hex}"
        with project_mutation_lease(path):
            was_active = self.active.read() == ref.identity
            with project_connection(path) as connection:
                summary = read_summary(connection)
                if (
                    summary.identity != ref.identity
                    or (expected_revision is not None and expected_revision != summary.revision)
                    or (expected_name is not None and expected_name != summary.name)
                ):
                    raise ProjectStorageConflict("project changed before deletion")
            parent.rename(tombstone)
            if was_active:
                try:
                    self.active.set(None, uuid4().hex)
                except OSError:
                    logger.exception("Project was removed, but its active preference could not be cleared")
        try:
            rmtree(tombstone)
        except OSError:
            logger.exception(
                "Project was removed; its isolated deletion directory is pending cleanup: %s", tombstone.name
            )
        return ProjectDeletion(
            summary.identity.value, summary.name, tuple(v.identity.value for v in summary.variants), was_active
        )
