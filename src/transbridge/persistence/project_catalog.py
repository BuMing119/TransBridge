"""Read-only V2 Project catalog projection.

The projection deliberately does not call ``ProjectRepository.load`` because a
load may migrate or quarantine an old/invalid record.  Start-center discovery
must never mutate user data.
"""

from __future__ import annotations

import os
from typing import Any

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
)
from transbridge.application.projects import ProjectCatalogEntry, ProjectCatalogSnapshot
from transbridge.persistence.v2.filesystem import PersistenceFilesystemPort, RepositoryPaths
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, SchemaValidationError
from transbridge.persistence.v2.repository import ProjectRepository
from transbridge.persistence.v2.schema import parse_json_bytes, validate_v2, version_of


class V2ProjectCatalog:
    """Project directory backed by ``project-catalog.json`` and repository records."""

    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
    ) -> None:
        self._filesystem = filesystem
        self._projects = projects
        self._paths = RepositoryPaths(root, filesystem)
        self._catalog_path = self._paths.guard(os.path.join(self._paths.root, "project-catalog.json"))
        self._active_path = self._paths.guard(os.path.join(self._paths.root, "active-project.json"))

    def list_projects(self) -> ProjectCatalogSnapshot:
        """Return a stable, caller-safe snapshot without performing any writes."""

        catalog, catalog_diagnostic = self._read_catalog()
        if catalog_diagnostic is not None:
            return ProjectCatalogSnapshot(diagnostics=(catalog_diagnostic,))
        if not catalog:
            return ProjectCatalogSnapshot()

        active_id, active_diagnostic = self._read_active_project_id()
        diagnostics = [active_diagnostic] if active_diagnostic is not None else []
        if active_id is not None and active_id not in {project_id for project_id, _name in catalog}:
            diagnostics.append(
                _diagnostic(
                    "ACTIVE_PROJECT_NOT_IN_CATALOG",
                    "当前工程指针未对应到工程目录中的记录。",
                )
            )

        entries = [self._entry(project_id, name, active_id) for project_id, name in catalog]
        entries.sort(key=lambda item: (not item.active, item.name.casefold(), item.project_id))
        return ProjectCatalogSnapshot(tuple(entries), tuple(diagnostics))

    def _read_catalog(self) -> tuple[tuple[tuple[str, str], ...], Diagnostic | None]:
        try:
            if not self._filesystem.exists(self._catalog_path):
                return (), None
            document = parse_json_bytes(self._filesystem.read_bytes(self._catalog_path))
            projects = document.get("projects")
            if document.get("schema_version") != 1 or not isinstance(projects, dict):
                raise ValueError("invalid Project catalog envelope")
            items: list[tuple[str, str]] = []
            names: set[str] = set()
            for raw_id, raw_entry in projects.items():
                if not isinstance(raw_id, str) or not isinstance(raw_entry, dict):
                    raise ValueError("invalid Project catalog entry")
                project_id = ProjectId(raw_id).value
                name = _display_name(raw_entry.get("name"))
                normalized_name = name.casefold()
                if normalized_name in names:
                    raise ValueError("duplicate Project catalog name")
                names.add(normalized_name)
                items.append((project_id, name))
            return tuple(items), None
        except (OSError, KeyError, SchemaValidationError, TypeError, ValueError):
            return (), _diagnostic(
                "PROJECT_CATALOG_INVALID",
                "工程目录无法读取或格式已损坏；当前未显示最近工程。",
            )

    def _read_active_project_id(self) -> tuple[str | None, Diagnostic | None]:
        try:
            if not self._filesystem.exists(self._active_path):
                return None, None
            document = parse_json_bytes(self._filesystem.read_bytes(self._active_path))
            if document.get("schema_version") != 1:
                raise ValueError("invalid active Project pointer schema")
            raw_project_id = document.get("project_id")
            if raw_project_id is None:
                return None, None
            if not isinstance(raw_project_id, str):
                raise ValueError("invalid active Project identity")
            return ProjectId(raw_project_id).value, None
        except (OSError, KeyError, SchemaValidationError, TypeError, ValueError):
            return None, _diagnostic(
                "ACTIVE_PROJECT_POINTER_INVALID",
                "当前工程指针无法读取；最近工程仍可手动打开。",
            )

    def _entry(self, project_id: str, name: str, active_id: str | None) -> ProjectCatalogEntry:
        ref = ProjectRef(ProjectId(project_id))
        path = self._projects.path_for(ref)
        reason = self._unavailable_reason(ref, path)
        return ProjectCatalogEntry(
            project_id=project_id,
            name=name,
            path=path,
            active=project_id == active_id,
            available=reason is None,
            reason=reason,
        )

    def _unavailable_reason(self, ref: ProjectRef, path: str) -> str | None:
        try:
            if not self._filesystem.exists(path):
                return "工程记录不存在。"
            document = parse_json_bytes(self._filesystem.read_bytes(path))
            if version_of(document) != SCHEMA_VERSION:
                return "工程记录版本不受支持。"
            validate_v2(document, ref)
            return None
        except (OSError, KeyError):
            return "工程记录暂时无法读取。"
        except SchemaValidationError:
            return "工程记录已损坏。"


def _display_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Project catalog name must be text")
    name = value.strip()
    if not name or len(name) > 80 or any(character in name for character in "\r\n\t"):
        raise ValueError("Project catalog name is invalid")
    return name


def _diagnostic(code: str, message: str) -> Diagnostic:
    return Diagnostic(
        code,
        message,
        severity=DiagnosticSeverity.WARNING,
        category=ErrorCategory.PREREQUISITE,
    )


__all__ = ["V2ProjectCatalog"]
