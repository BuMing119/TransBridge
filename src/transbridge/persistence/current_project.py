"""Open current-schema Project records for GUI and other entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId
from transbridge.application.projects import (
    DirtyDecision,
    GuiProjectCommandFacade,
    PreparedProjectSource,
    PreparedSourceHydration,
    ProjectSourceRequest,
)
from transbridge.application.projects.source_content import authoritative_baseline_sources
from transbridge.application.projects.source_registry import legacy_source_role
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_recovery import (
    ProjectRecoverySnapshot,
    load_recovery_snapshot,
    source_recovery_diagnostic,
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
)
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.models import PersistenceV2Error
from transbridge.persistence.v2.schema import parse_json_bytes, version_of

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
    ) -> None:
        self._active_pointer = Path(root) / "active-project.json"
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._commands = commands
        self._baseline_loader = baseline_loader or (
            lambda source, context: _load_baseline(source, context, source_preparer=source_preparer)
        )

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
    ) -> OperationResult[PreparedCurrentProject]:
        """Perform file I/O and source parsing without mutating active GUI state."""

        try:
            selected = Path(path).resolve(strict=True)
            document = parse_json_bytes(selected.read_bytes())
            if (
                version_of(document) not in {2, SCHEMA_VERSION}
                or document.get("entity_type") != EntityKind.PROJECT.value
            ):
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
                project = self._projects.read_snapshot(project_ref)
            except (OSError, PersistenceV2Error) as exc:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_RECORD_UNAVAILABLE",
                    "项目记录不可用或为只读状态。",
                    cause=exc,
                ) from exc
            variant_id = project.envelope.data.get("active_variant_id")
            if not isinstance(variant_id, str) or not variant_id:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "ACTIVE_VARIANT_REQUIRED",
                    "项目没有活动版本。",
                )
            variant_ref = VariantRef(VariantId(variant_id), project_ref.identity)
            if not Path(self._variants.path_for(variant_ref)).exists():
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "VARIANT_RECORD_UNAVAILABLE",
                    "项目的活动版本记录不存在。",
                )
            sources = tuple(project.envelope.data.get("sources", ()))
            baseline_sources = authoritative_baseline_sources(
                sources, tuple(project.envelope.data.get("source_relations", ()))
            )
            loaded_sources = []
            source_diagnostics = []
            for source in baseline_sources:
                try:
                    loaded_sources.append(self._baseline_loader(source, context))
                except (OSError, DomainError) as exc:
                    source_diagnostics.append(source_recovery_diagnostic(source, exc))
                    # Recovery only needs persisted translations, not more source diagnostics.
                    break
            baselines = tuple(
                item.baseline if isinstance(item, PreparedProjectSource) else item for item in loaded_sources
            )
            hydrations = tuple(
                item.hydration
                for item in loaded_sources
                if isinstance(item, PreparedProjectSource) and item.hydration is not None
            )
            if sources and not baselines and not source_diagnostics:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SOURCE_BASELINE_REQUIRED",
                    "项目没有可验证的源文件基线。",
                )
            variant_refs = tuple(
                VariantRef(VariantId(str(value)), project_ref.identity)
                for value in project.envelope.data.get("variant_ids", ())
            )
            recovery = None
            if source_diagnostics:
                recovery = load_recovery_snapshot(
                    str(selected),
                    str(project.envelope.data["name"]),
                    variant_ref,
                    self._variants,
                    tuple(source_diagnostics),
                    context,
                )
            return OperationResult.completed(
                PreparedCurrentProject(
                    project_ref,
                    variant_ref,
                    variant_refs,
                    baselines,
                    str(project.envelope.data["name"]),
                    sources,
                    hydrations,
                    recovery,
                ),
                diagnostics=tuple(source_diagnostics),
                run_id=context.run_id,
            )
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
    ) -> OperationResult[dict]:
        """Commit a prepared Project on the caller thread."""

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
            for variant_ref in prepared.variant_refs:
                self._baselines.register(
                    prepared.project_ref,
                    variant_ref,
                    prepared.baselines,
                    allow_empty=not prepared.sources,
                )
            switched = self._commands.switch_v2(
                prepared.project_ref,
                prepared.variant_ref,
                context,
                dirty_decision=dirty_decision,
            )
            if not switched.is_success:
                return switched
            return OperationResult.completed(
                {
                    "project_id": prepared.project_ref.identity.value,
                    "variant_id": prepared.variant_ref.identity.value,
                    "name": prepared.name,
                    "sources": prepared.sources,
                    "hydrations": prepared.hydrations,
                },
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)


def _load_baseline(
    source: dict,
    context: RequestContext,
    *,
    source_preparer=None,
) -> PreparedProjectSource:
    path = Path(str(source.get("location") or source.get("path") or "")).resolve(strict=True)
    raw_format = source.get("format_id", FormatId.PLUGIN_SSE.value)
    try:
        format_id = FormatId(str(raw_format))
    except ValueError as exc:
        raise DomainError(
            ErrorCategory.INPUT,
            "SOURCE_FORMAT_UNSUPPORTED",
            "项目来源记录包含不支持的格式。",
        ) from exc
    options = tuple((source.get("format_options") or source.get("options") or {}).items())
    role = legacy_source_role(source) or "primary"
    preparer = source_preparer or TranslationIoProjectSourcePreparer()
    return preparer.prepare_source(
        ProjectSourceRequest(
            str(path),
            format_hint=format_id,
            expected_fingerprint=source.get("fingerprint"),
            options=options,
        ),
        context,
        role=role,
        common_options=(),
    )


__all__ = ["CurrentProjectOpener", "PreparedCurrentProject", "PROJECT_FILE_FILTER"]
