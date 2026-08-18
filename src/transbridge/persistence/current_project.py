"""Restore the active Project using current persistence records only."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, OperationResult, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.projects import DirtyDecision, GuiProjectCommandFacade
from transbridge.persistence.v2 import (
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SourceBaseline,
    SourceFingerprint,
    VariantDto,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantRepository,
    VariantSnapshot,
)
from transbridge.persistence.v2.baselines import BaselineRegistry

BaselineLoader = Callable[[dict, RequestContext], SourceBaseline]


class CurrentProjectActivator:
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
        self._pointer = Path(root) / "active-project.json"
        self._projects = projects
        self._variants = variants
        self._baselines = baselines
        self._commands = commands
        self._baseline_loader = baseline_loader or _load_baseline

    def restore(self, context: RequestContext) -> OperationResult[dict | None]:
        try:
            pointer = json.loads(self._pointer.read_text(encoding="utf-8"))
            project_ref = ProjectRef(ProjectId(str(pointer["project_id"])))
            variant_ref = VariantRef(VariantId(str(pointer["variant_id"])), project_ref.identity)
            project_loaded = self._projects.load(project_ref)
            variant_loaded = self._variants.load(variant_ref)
            if not isinstance(project_loaded, LoadedRecord) or not isinstance(project_loaded.value, ProjectDto):
                raise ValueError("the active Project record is unavailable")
            if not isinstance(variant_loaded, LoadedRecord) or not isinstance(variant_loaded.value, VariantDto):
                raise ValueError("the active Variant record is unavailable")
            stored = VariantSnapshot.from_dto(variant_loaded.value, variant_ref)
            baselines = tuple(
                self._baseline_loader(source, context)
                for source in project_loaded.value.envelope.data.get("sources", ())
            )
            current_fingerprints = {item.fingerprint.namespace: item.fingerprint.sha256 for item in baselines}
            stored_fingerprints = {item.namespace: item.sha256 for item in stored.source_fingerprints}
            if current_fingerprints != stored_fingerprints:
                raise ValueError("a project source changed after the current Variant was saved")
            self._baselines.register(project_ref, variant_ref, baselines)
            return self._commands.switch_v2(
                project_ref,
                variant_ref,
                context,
                dirty_decision=DirtyDecision.SAVE,
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)


def _load_baseline(source: dict, context: RequestContext) -> SourceBaseline:
    path = Path(str(source["path"])).resolve(strict=True)
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
        raise ValueError("the active Project source could not be parsed")
    namespace = parsed.entries[0].identity.namespace
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


__all__ = ["CurrentProjectActivator"]
