"""Authoritative Project rename/delete commands."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.project_catalog_document import project_display_name
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope

from .lifecycle import ProjectLifecycleService


@dataclass(frozen=True, slots=True)
class ProjectDeletion:
    project_id: str
    name: str
    variant_ids: tuple[str, ...]
    was_active: bool


class ProjectManagementPersistencePort(Protocol):
    def delete(
        self,
        ref: ProjectRef,
        *,
        expected_revision: int | None = None,
        expected_name: str | None = None,
    ) -> ProjectDeletion: ...


class ProjectManagementCommands:
    def __init__(
        self,
        lifecycle: ProjectLifecycleService,
        persistence: ProjectManagementPersistencePort,
        baselines: BaselineRegistry,
    ) -> None:
        self._lifecycle = lifecycle
        self._persistence = persistence
        self._baselines = baselines

    def rename(self, name: str, context: RequestContext) -> OperationResult[dict[str, object]]:
        try:
            active = self._lifecycle.active
            if active is None:
                raise DomainError(ErrorCategory.PREREQUISITE, "ACTIVE_PROJECT_REQUIRED", "请先打开一个本地工程。")
            if context.project_id not in (None, active.project_ref.identity.value):
                raise DomainError(ErrorCategory.PERMISSION, "PROJECT_CONTEXT_MISMATCH", "工程上下文已经改变。")
            canonical_name = project_display_name(name)
            if active.project.envelope.data.get("name") == canonical_name:
                return OperationResult.completed(
                    {
                        "project_id": active.project_ref.identity.value,
                        "project_revision": active.project.envelope.revision,
                        "name": canonical_name,
                    },
                    run_id=context.run_id,
                )
            envelope = active.project.envelope
            data = deepcopy(envelope.data)
            data["name"] = canonical_name
            project = ProjectDto(
                SchemaEnvelope(
                    envelope.schema_version,
                    envelope.entity_type,
                    envelope.identity,
                    envelope.revision + 1,
                    data,
                )
            )
            result = self._lifecycle.commit_project_update(project, envelope.revision, context)
            if result.outcome is not OperationOutcome.COMPLETED:
                return result
            return OperationResult.completed(
                {
                    "project_id": active.project_ref.identity.value,
                    "project_revision": project.envelope.revision,
                    "name": canonical_name,
                },
                diagnostics=result.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, DomainError)
                else DomainError(
                    ErrorCategory.INPUT,
                    "PROJECT_RENAME_FAILED",
                    str(exc),
                    cause=exc,
                )
            )
            return OperationResult.failed(error, run_id=context.run_id)

    def delete(
        self,
        project_id: str,
        context: RequestContext,
        *,
        expected_name: str,
    ) -> OperationResult[dict[str, object]]:
        try:
            project_ref = ProjectRef(ProjectId(project_id))
            active = self._lifecycle.active
            is_active = active is not None and active.project_ref == project_ref
            expected_revision = active.persisted_project_revision if is_active else None
            deletion = self._persistence.delete(
                project_ref,
                expected_revision=expected_revision,
                expected_name=project_display_name(expected_name),
            )
            for variant_id in deletion.variant_ids:
                self._baselines.remove(project_ref, VariantRef(VariantId(variant_id), project_ref.identity))
            lifecycle_result = self._lifecycle.accept_project_deletion(project_ref, context)
            return OperationResult.completed(
                {
                    "project_id": deletion.project_id,
                    "name": deletion.name,
                    "active_removed": bool(lifecycle_result.value and lifecycle_result.value["active_removed"]),
                    "external_sources_deleted": False,
                },
                diagnostics=lifecycle_result.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, DomainError)
                else DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_DELETE_FAILED",
                    str(exc),
                    cause=exc,
                )
            )
            return OperationResult.failed(error, run_id=context.run_id)


__all__ = ["ProjectDeletion", "ProjectManagementCommands", "ProjectManagementPersistencePort"]
