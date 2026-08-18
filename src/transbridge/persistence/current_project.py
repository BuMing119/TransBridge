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
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.projects import DirtyDecision, GuiProjectCommandFacade
from transbridge.persistence.v2 import (
    EntityKind,
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SourceBaseline,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantRepository,
)
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.schema import parse_json_bytes, version_of

PROJECT_FILE_FILTER = "项目 JSON (*.json);;所有文件 (*)"
BaselineLoader = Callable[[dict, RequestContext], SourceBaseline]


@dataclass(frozen=True, slots=True)
class PreparedCurrentProject:
    project_ref: ProjectRef
    variant_ref: VariantRef
    variant_refs: tuple[VariantRef, ...]
    baselines: tuple[SourceBaseline, ...]
    name: str
    sources: tuple[dict, ...]


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
    ) -> None:
        self._active_pointer = Path(root) / "active-project.json"
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._commands = commands
        self._baseline_loader = baseline_loader or _load_baseline

    @property
    def directory(self) -> str:
        return str(Path(self._projects.root) / "projects")

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
            if version_of(document) != 2 or document.get("entity_type") != EntityKind.PROJECT.value:
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
            loaded = self._projects.load(project_ref)
            if not isinstance(loaded, LoadedRecord) or not isinstance(loaded.value, ProjectDto):
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "PROJECT_RECORD_UNAVAILABLE",
                    "项目记录不可用或为只读状态。",
                )
            project = loaded.value
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
            baselines = tuple(
                self._baseline_loader(source, context) for source in project.envelope.data.get("sources", ())
            )
            if not baselines:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SOURCE_BASELINE_REQUIRED",
                    "项目没有可验证的源文件基线。",
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
                    baselines,
                    str(project.envelope.data["name"]),
                    tuple(project.envelope.data.get("sources", ())),
                ),
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
            for variant_ref in prepared.variant_refs:
                self._baselines.register(
                    prepared.project_ref,
                    variant_ref,
                    prepared.baselines,
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
                },
                run_id=context.run_id,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)


def _load_baseline(source: dict, context: RequestContext) -> SourceBaseline:
    path = Path(str(source.get("path", ""))).resolve(strict=True)
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(path), path.name, path.stat().st_size),
            context,
            format_hint=FormatId.PLUGIN_SSE,
            options=(("skip_empty", False),),
        )
    )
    allowed_partial = parsed.outcome is OperationOutcome.PARTIAL and all(
        item.code == "SOURCE_LOCATOR_CONFLICT" for item in parsed.diagnostics
    )
    if (parsed.outcome is not OperationOutcome.COMPLETED and not allowed_partial) or parsed.source_snapshot is None:
        raise DomainError(
            ErrorCategory.PREREQUISITE,
            "SOURCE_BASELINE_UNAVAILABLE",
            "项目源文件无法解析为可验证基线。",
        )
    if parsed.entries:
        namespace = parsed.entries[0].identity.namespace
    else:
        namespace_value = dict(parsed.source_snapshot.metadata).get("source_namespace")
        if not isinstance(namespace_value, str):
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "SOURCE_IDENTITY_UNAVAILABLE",
                "空源文件没有可验证的来源身份。",
            )
        namespace = SourceNamespace(namespace_value)
    return SourceBaseline(
        SourceFingerprint(namespace, parsed.source_snapshot.sha256),
        tuple(
            VariantEntryState(
                entry.identity,
                entry.translation,
                entry.stage,
                provenance=entry.provenance,
                revision=entry.revision,
            )
            for entry in parsed.entries
        ),
    )


__all__ = ["CurrentProjectOpener", "PreparedCurrentProject", "PROJECT_FILE_FILTER"]
