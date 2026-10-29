"""Open current or safely migratable Project records for GUI and other entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
from types import EllipsisType

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationResult,
    RequestContext,
)
from transbridge.application.projects import (
    DirtyDecision,
    GuiProjectCommandFacade,
    PreparedProjectSource,
    PreparedSourceHydration,
)
from transbridge.application.projects.source_inputs import MissingProjectSourceInput
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_recovery import (
    ProjectRecoverySnapshot,
    validate_recovery_context,
)
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    EntityKind,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SourceBaseline,
    VariantId,
    VariantRef,
    VariantRepository,
    VariantSnapshot,
)
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.models import PersistenceV2Error
from transbridge.persistence.v2.schema import parse_json_bytes, version_of
from transbridge.persistence.v2.validated_reads import reuse_validated_reads

from .prepared_project_read import PreparedProjectRead, use_prepared_project
from .project_source_inputs import load_project_source_inputs
from .saved_project_sources import SavedProjectSourcePreparer
from .source_artifact_store import ProjectSourceArtifactStore

PROJECT_FILE_FILTER = "项目 JSON (*.json);;所有文件 (*)"
BaselineLoader = Callable[[dict, RequestContext], SourceBaseline | PreparedProjectSource]


@dataclass(frozen=True, slots=True)
class PreparedCurrentProject:
    project_ref: ProjectRef
    variant_ref: VariantRef
    variant_refs: tuple[VariantRef, ...]
    baselines: tuple[SourceBaseline, ...]
    name: str
    sources: tuple[dict, ...]
    hydrations: tuple[PreparedSourceHydration, ...] = ()
    recovery: ProjectRecoverySnapshot | None = None
    prepared_read: PreparedProjectRead | None = None
    missing_inputs: tuple[MissingProjectSourceInput, ...] = ()


class CurrentProjectOpener:
    def __init__(
        self,
        root: str,
        projects: ProjectRepository,
        variants: VariantRepository,
        baselines: BaselineRegistry,
        commands: GuiProjectCommandFacade,
        *,
        baseline_loader: BaselineLoader | None = None,
        source_preparer=None,
        prepare_projection=None,
        discard_prepared_projection=None,
        source_artifacts=None,
    ) -> None:
        self._active_pointer = Path(root) / "active-project.json"
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._commands = commands
        self._baseline_loader = baseline_loader
        self._source_artifacts = source_artifacts or ProjectSourceArtifactStore(root)
        self._source_preparer = (
            source_preparer
            if isinstance(source_preparer, SavedProjectSourcePreparer)
            else SavedProjectSourcePreparer(
                source_preparer or TranslationIoProjectSourcePreparer(), self._source_artifacts
            )
        )
        self._prepare_projection = prepare_projection
        self._discard_prepared_projection = discard_prepared_projection

    @property
    def directory(self) -> str:
        return str(Path(self._projects.root) / "projects")

    @property
    def has_active_reference(self) -> bool:
        """Whether startup has an authoritative Project pointer to restore."""

        return self._active_pointer.is_file()

    def open_path(
        self,
        path: str | os.PathLike[str],
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
    ) -> OperationResult[dict]:
        prepared = self.prepare_path(path, context)
        if not prepared.is_success or prepared.value is None:
            return OperationResult(
                prepared.outcome,
                diagnostics=prepared.diagnostics,
                counts=prepared.counts,
                run_id=prepared.run_id,
            )
        return self.activate(prepared.value, context, dirty_decision=dirty_decision)

    def prepare_path(
        self,
        path: str | os.PathLike[str],
        context: RequestContext,
        *,
        target_variant_id: str | None = None,
    ) -> OperationResult[PreparedCurrentProject]:
        """Perform file I/O and source parsing without mutating active GUI state."""

        with reuse_validated_reads() as evidence:
            return self._prepare_path(path, context, target_variant_id=target_variant_id, evidence=evidence)

    def _prepare_path(self, path, context, *, target_variant_id, evidence):

        try:
            selected = Path(path).resolve(strict=True)
            document = parse_json_bytes(selected.read_bytes())
            if version_of(document) > SCHEMA_VERSION or document.get("entity_type") != EntityKind.PROJECT.value:
                raise DomainError(
                    ErrorCategory.INPUT,
                    "PROJECT_RECORD_REQUIRED",
                    "请选择 projects 根目录中的项目 JSON，不要选择 variants 目录中的版本 JSON。",
                )
            project_ref = ProjectRef(ProjectId(str(document.get("id", ""))))
            expected = Path(self._projects.path_for(project_ref)).resolve(strict=False)
            if os.path.normcase(str(selected)) != os.path.normcase(str(expected)):
                raise DomainError(
                    ErrorCategory.PERMISSION,
                    "PROJECT_RECORD_OUTSIDE_REPOSITORY",
                    "所选项目不属于当前数据目录。",
                )
            try:
                project_record = self._projects.read_snapshot_record(project_ref)
                project = project_record.value
            except (OSError, PersistenceV2Error) as exc:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_RECORD_UNAVAILABLE",
                    "项目记录不可用或为只读状态。",
                    cause=exc,
                ) from exc
            variant_id = target_variant_id or project.envelope.data.get("active_variant_id")
            if not isinstance(variant_id, str) or not variant_id:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_VARIANT_REQUIRED",
                    "项目没有活动版本。",
                )
            if target_variant_id is not None and target_variant_id not in project.envelope.data.get("variant_ids", ()):
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_VARIANT_UNAVAILABLE",
                    "此对话绑定的版本已不存在或不属于该工程，请选择仍可用的工程版本。",
                )
            variant_ref = VariantRef(VariantId(variant_id), project_ref.identity)
            validate_recovery_context(variant_ref, context)
            if not Path(self._variants.path_for(variant_ref)).exists():
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "VARIANT_RECORD_UNAVAILABLE",
                    "项目的活动版本记录不存在。",
                )
            try:
                variant_record = self._variants.read_snapshot_record(variant_ref)
                stored = VariantSnapshot.from_dto(variant_record.value, variant_ref)
            except (OSError, PersistenceV2Error, ValueError) as exc:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "VARIANT_RECORD_UNAVAILABLE",
                    "保存的版本记录不可读取，无法打开工程或恢复视图。",
                    cause=exc,
                ) from exc
            inputs = load_project_source_inputs(
                project, stored, self._source_artifacts, context, baseline_loader=self._baseline_loader
            )
            variant_refs = tuple(
                VariantRef(VariantId(str(value)), project_ref.identity)
                for value in project.envelope.data.get("variant_ids", ())
            )
            return OperationResult.completed(
                PreparedCurrentProject(
                    project_ref,
                    variant_ref,
                    variant_refs,
                    inputs.baselines,
                    str(project.envelope.data["name"]),
                    inputs.sources,
                    inputs.hydrations,
                    None,
                    PreparedProjectRead.capture(
                        self._projects,
                        project_record,
                        self._variants,
                        variant_record,
                        stored,
                        evidence,
                        bound_sources=inputs.sources,
                        source_baselines=inputs.baselines,
                        missing_inputs=inputs.missing_inputs,
                        hydrations=inputs.hydrations,
                    ),
                    inputs.missing_inputs,
                ),
                diagnostics=inputs.diagnostics,
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare_identity(
        self,
        project_id: str,
        variant_id: str,
        context: RequestContext,
    ) -> OperationResult[PreparedCurrentProject]:
        """Prepare one exact Project/Variant without opening an intermediate version."""

        try:
            project_ref = ProjectRef(ProjectId(project_id))
            VariantId(variant_id)
            return self.prepare_path(self._projects.path_for(project_ref), context, target_variant_id=variant_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def prepare_active(self, context: RequestContext) -> OperationResult[PreparedCurrentProject]:
        """Prepare the persisted active Project without committing it."""

        try:
            pointer = json.loads(self._active_pointer.read_text(encoding="utf-8"))
            project_ref = ProjectRef(ProjectId(str(pointer["project_id"])))
            expected_variant_id = str(pointer["variant_id"])
            prepared = self.prepare_path(self._projects.path_for(project_ref), context)
            if not prepared.is_success or prepared.value is None:
                return prepared
            if prepared.value.variant_ref.identity.value != expected_variant_id:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "ACTIVE_PROJECT_POINTER_MISMATCH",
                    "活动项目指针与项目记录中的活动版本不一致。",
                )
            return prepared
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def activate(
        self,
        prepared: PreparedCurrentProject,
        context: RequestContext,
        *,
        dirty_decision: DirtyDecision | None = None,
        prepare_view: Callable | None = None,
        expected_active_signature: tuple[str, int, str | None, int | None] | None | EllipsisType = ...,
    ) -> OperationResult[dict]:
        """Commit a prepared Project on the caller thread."""

        captured = None
        committed = False
        try:
            if prepared.recovery is not None:
                # A recovery result is deliberately detached from the editable
                # lifecycle. Do not save/discard the current project or replace
                # its baseline registry with a partial source set.
                validate_recovery_context(prepared.recovery.variant.ref, context)
                return OperationResult.completed(
                    {
                        "project_id": prepared.project_ref.identity.value,
                        "variant_id": prepared.variant_ref.identity.value,
                        "name": prepared.name,
                        "read_only": True,
                        "recovery": prepared.recovery,
                    },
                    diagnostics=prepared.recovery.diagnostics,
                    run_id=context.run_id,
                )
            captured = self._baselines.capture_catalog(prepared.project_ref, prepared.variant_refs)
            self._baselines.replace_many(
                prepared.project_ref,
                prepared.variant_refs,
                prepared.baselines,
                allow_empty=True,
            )
            view_payload = None
            missing_inputs = prepared.missing_inputs

            def prepare_candidate(candidate):
                nonlocal view_payload, missing_inputs
                missing_inputs = candidate.missing_inputs
                if prepare_view is not None:
                    from transbridge.application.projections.adapters import project_projection

                    project_view = self._prepare_projection or project_projection
                    view_payload = prepare_view(project_view(candidate))

            if prepared.prepared_read is None:
                raise ValueError("Opening requires its prepared input evidence")
            with use_prepared_project(prepared.prepared_read):
                switched = self._commands.switch_v2(
                    prepared.project_ref,
                    prepared.variant_ref,
                    context,
                    dirty_decision=dirty_decision,
                    prepare_candidate=prepare_candidate,
                    expected_active_signature=expected_active_signature,
                )
            if not switched.is_success:
                return switched
            committed = True
            return OperationResult.completed(
                {
                    "project_id": prepared.project_ref.identity.value,
                    "variant_id": prepared.variant_ref.identity.value,
                    "name": prepared.name,
                    "sources": prepared.sources,
                    "hydrations": prepared.hydrations,
                    "view_payload": view_payload,
                    "missing_inputs": missing_inputs,
                },
                diagnostics=tuple(item.diagnostic for item in missing_inputs),
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)
        finally:
            if self._discard_prepared_projection is not None:
                self._discard_prepared_projection()
            if captured is not None and not committed:
                self._baselines.restore_catalog(prepared.project_ref, captured, expected_baselines=prepared.baselines)


__all__ = ["CurrentProjectOpener", "PreparedCurrentProject", "PROJECT_FILE_FILTER"]
