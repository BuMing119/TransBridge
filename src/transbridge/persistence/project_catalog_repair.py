"""Startup repair for a missing derived Project catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from threading import RLock

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.persistence.v2.atomic_documents import AtomicDocumentStore
from transbridge.persistence.v2.filesystem import PersistenceFilesystemPort, RepositoryPaths
from transbridge.persistence.v2.ids import ProjectId, ProjectRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, PathBoundaryError, SchemaValidationError
from transbridge.persistence.v2.repository import ProjectRepository
from transbridge.persistence.v2.schema import parse_json_bytes, serialize_document, validate_v2, version_of

from .project_catalog_document import (
    ProjectCatalogRecord,
    build_project_catalog,
    parse_project_catalog,
    project_display_name,
    project_name_key,
)


class ProjectCatalogRepairStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    NO_PROJECTS = "no_projects"
    REBUILT = "rebuilt"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProjectCatalogRepairReport:
    status: ProjectCatalogRepairStatus
    recovered_count: int = 0
    skipped_count: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.recovered_count < 0 or self.skipped_count < 0:
            raise ValueError("Project catalog repair counts must not be negative")


class ProjectCatalogRepairService:
    """Rebuild a missing catalog from strict, canonical current Project records."""

    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
    ) -> None:
        self._filesystem = filesystem
        self._projects = projects
        self._paths = RepositoryPaths(root, filesystem)
        self._documents = AtomicDocumentStore(root, filesystem)
        self._lock = RLock()

    def repair_if_missing(self) -> ProjectCatalogRepairReport:
        with self._lock:
            try:
                catalog_path = self._documents.path("project-catalog.json")
                projects_directory = self._paths.guard(os.path.join(self._paths.root, "projects"))
            except (OSError, PathBoundaryError, ValueError):
                return ProjectCatalogRepairReport(
                    ProjectCatalogRepairStatus.FAILED,
                    diagnostics=(
                        _diagnostic(
                            "PROJECT_CATALOG_REPAIR_PATH_INVALID",
                            "本地工程目录路径未通过安全验证，工程目录索引未重建。",
                            error=True,
                            retryable=True,
                        ),
                    ),
                )

            try:
                if self._filesystem.exists(catalog_path):
                    return self._existing_catalog_report(catalog_path)
            except OSError:
                return ProjectCatalogRepairReport(
                    ProjectCatalogRepairStatus.FAILED,
                    diagnostics=(
                        _diagnostic(
                            "PROJECT_CATALOG_REPAIR_CATALOG_CHECK_FAILED",
                            "无法确认工程目录索引状态，本次启动未尝试重建。",
                            error=True,
                            retryable=True,
                        ),
                    ),
                )

            try:
                candidates = tuple(
                    path for path in self._filesystem.list_files(projects_directory) if path.lower().endswith(".json")
                )
            except OSError:
                return ProjectCatalogRepairReport(
                    ProjectCatalogRepairStatus.FAILED,
                    diagnostics=(
                        _diagnostic(
                            "PROJECT_CATALOG_REPAIR_DISCOVERY_FAILED",
                            "无法扫描本地工程记录，工程目录索引未重建。",
                            error=True,
                            retryable=True,
                        ),
                    ),
                )

            records: list[ProjectCatalogRecord] = []
            diagnostics: list[Diagnostic] = []
            identities: set[str] = set()
            names: dict[str, str] = {}
            for candidate in candidates:
                record, diagnostic = self._read_candidate(candidate)
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
                    continue
                if record is None:
                    return ProjectCatalogRepairReport(
                        ProjectCatalogRepairStatus.FAILED,
                        skipped_count=len(diagnostics),
                        diagnostics=tuple(diagnostics)
                        + (
                            _diagnostic(
                                "PROJECT_CATALOG_REPAIR_INTERNAL_ERROR",
                                "工程目录索引重建遇到内部状态错误，未发布任何结果。",
                                error=True,
                            ),
                        ),
                    )
                if record.project_id in identities:
                    return self._conflict_report(
                        diagnostics,
                        "PROJECT_CATALOG_REPAIR_ID_CONFLICT",
                        "多个合法工程记录声明了相同的工程 ID，未自动重建目录索引。",
                    )
                existing_id = names.get(record.name_key)
                if existing_id is not None and existing_id != record.project_id:
                    return self._conflict_report(
                        diagnostics,
                        "PROJECT_CATALOG_REPAIR_NAME_CONFLICT",
                        "多个合法工程使用了相同名称，未自动重建目录索引。",
                    )
                identities.add(record.project_id)
                names[record.name_key] = record.project_id
                records.append(record)

            if not records:
                return ProjectCatalogRepairReport(
                    ProjectCatalogRepairStatus.NO_PROJECTS,
                    skipped_count=len(diagnostics),
                    diagnostics=tuple(diagnostics),
                )

            stable_records = tuple(sorted(records, key=lambda item: item.project_id))
            document = build_project_catalog(stable_records)
            payload = serialize_document(document)
            try:
                if self._filesystem.exists(catalog_path):
                    return self._existing_catalog_report(catalog_path)
                self._documents.write_json(
                    "project-catalog.json",
                    document,
                    f"project-catalog-repair-{hashlib.sha256(payload).hexdigest()}",
                )
                published = parse_project_catalog(self._filesystem.read_bytes(catalog_path))
                if published != stable_records:
                    raise OSError("published Project catalog did not match the verified repair draft")
            except (OSError, SchemaValidationError, ValueError):
                return ProjectCatalogRepairReport(
                    ProjectCatalogRepairStatus.FAILED,
                    skipped_count=len(diagnostics),
                    diagnostics=tuple(diagnostics)
                    + (
                        _diagnostic(
                            "PROJECT_CATALOG_REPAIR_PUBLISH_FAILED",
                            "工程目录索引重建未能安全发布，可在下次启动时重试。",
                            error=True,
                            retryable=True,
                        ),
                    ),
                )

            return ProjectCatalogRepairReport(
                ProjectCatalogRepairStatus.REBUILT,
                recovered_count=len(stable_records),
                skipped_count=len(diagnostics),
                diagnostics=tuple(diagnostics),
            )

    def _existing_catalog_report(self, catalog_path: str) -> ProjectCatalogRepairReport:
        try:
            parse_project_catalog(self._filesystem.read_bytes(catalog_path))
        except (OSError, SchemaValidationError, TypeError, ValueError):
            return ProjectCatalogRepairReport(
                ProjectCatalogRepairStatus.BLOCKED,
                diagnostics=(
                    _diagnostic(
                        "PROJECT_CATALOG_REPAIR_EXISTING_INVALID",
                        "工程目录索引已存在但无法验证；为保留现场，未自动覆盖。",
                        error=True,
                    ),
                ),
            )
        return ProjectCatalogRepairReport(ProjectCatalogRepairStatus.NOT_NEEDED)

    def _read_candidate(
        self,
        candidate: str,
    ) -> tuple[ProjectCatalogRecord | None, Diagnostic | None]:
        candidate_name = os.path.basename(candidate)
        try:
            canonical_candidate = self._paths.guard(candidate)
        except (OSError, PathBoundaryError, ValueError):
            return None, _skipped_candidate("PROJECT_CATALOG_REPAIR_NONCANONICAL", candidate_name)
        try:
            document = parse_json_bytes(self._filesystem.read_bytes(canonical_candidate))
            version = version_of(document)
        except OSError:
            return None, _skipped_candidate("PROJECT_CATALOG_REPAIR_CANDIDATE_UNREADABLE", candidate_name)
        except (SchemaValidationError, TypeError, ValueError):
            return None, _skipped_candidate("PROJECT_CATALOG_REPAIR_CANDIDATE_INVALID", candidate_name)
        if version != SCHEMA_VERSION:
            return None, _skipped_candidate("PROJECT_CATALOG_REPAIR_SCHEMA_UNSUPPORTED", candidate_name)

        try:
            raw_id = document.get("id")
            if not isinstance(raw_id, str):
                raise ValueError("Project identity is missing")
            ref = ProjectRef(ProjectId(raw_id))
            expected_path = self._projects.path_for(ref)
            if os.path.normcase(canonical_candidate) != os.path.normcase(expected_path):
                raise ValueError("Project path is not canonical")
            project = validate_v2(document, ref)
            name = project_display_name(project.envelope.data.get("name"))
            return ProjectCatalogRecord(ref.identity.value, name, project_name_key(name)), None
        except (KeyError, SchemaValidationError, TypeError, ValueError):
            return None, _skipped_candidate("PROJECT_CATALOG_REPAIR_CANDIDATE_INVALID", candidate_name)

    @staticmethod
    def _conflict_report(
        diagnostics: list[Diagnostic],
        code: str,
        message: str,
    ) -> ProjectCatalogRepairReport:
        return ProjectCatalogRepairReport(
            ProjectCatalogRepairStatus.BLOCKED,
            skipped_count=len(diagnostics),
            diagnostics=tuple(diagnostics) + (_diagnostic(code, message, error=True),),
        )


def _skipped_candidate(code: str, candidate_name: str) -> Diagnostic:
    return _diagnostic(
        code,
        "一个本地工程记录未通过安全验证，已跳过且未修改原文件。",
        details=(("candidate", candidate_name),),
    )


def _diagnostic(
    code: str,
    message: str,
    *,
    error: bool = False,
    retryable: bool = False,
    details: tuple[tuple[str, str], ...] = (),
) -> Diagnostic:
    return Diagnostic(
        code,
        message,
        severity=DiagnosticSeverity.ERROR if error else DiagnosticSeverity.WARNING,
        category=ErrorCategory.PREREQUISITE,
        retryable=retryable,
        details=details,
    )


__all__ = [
    "ProjectCatalogRepairReport",
    "ProjectCatalogRepairService",
    "ProjectCatalogRepairStatus",
]
