"""Load available saved source inputs without changing a Variant's saved state."""

from dataclasses import dataclass, replace
from pathlib import PureWindowsPath

from transbridge.application.contracts import Diagnostic, DomainError, ErrorCategory
from transbridge.application.preflight.sources import source_reference
from transbridge.application.projects.provisioning_models import PreparedProjectSource, PreparedSourceHydration
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.application.projects.source_inputs import MissingProjectSourceInput, missing_inputs_for
from transbridge.persistence.v2.variant import SourceBaseline

from .project_recovery import source_recovery_diagnostic
from .saved_project_sources import load_saved_project_source


@dataclass(frozen=True, slots=True)
class LoadedProjectSourceInputs:
    sources: tuple[dict, ...]
    baselines: tuple[SourceBaseline, ...]
    hydrations: tuple[PreparedSourceHydration, ...]
    missing_inputs: tuple[MissingProjectSourceInput, ...]
    diagnostics: tuple[Diagnostic, ...]


def load_project_source_inputs(project, snapshot, artifacts, context, *, baseline_loader=None):
    """Inspect every registered saved artifact; optional references are never parsed."""
    sources = tuple(source_reference(source) for source in project.envelope.data.get("sources", ()))
    baseline_sources = authoritative_baseline_sources(sources, tuple(project.envelope.data.get("source_relations", ())))
    loaded_sources = []
    bound_sources = [dict(source) for source in sources]
    failures = {}
    warnings = []
    for source in baseline_sources:
        try:
            if source.get("source_artifact_id"):
                loaded = load_saved_project_source(artifacts, source, snapshot.source_fingerprints)
            elif baseline_loader is not None:
                loaded = baseline_loader(source, context)
            else:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SOURCE_BASELINE_REQUIRED",
                    "工程尚未保存可用的来源内容；已有译文可以继续编辑和保存，需要时请补充输入。",
                )
            loaded_sources.append(loaded)
            if isinstance(loaded, PreparedProjectSource):
                index = next(index for index, item in enumerate(sources) if item is source)
                if not bound_sources[index].get("display_name") and loaded.hydration is not None:
                    captured = loaded.hydration.source_snapshot
                    bound_sources[index]["display_name"] = (
                        captured.source.display_name or PureWindowsPath(loaded.hydration.location).name
                    )
                artifact_id = loaded.to_dict().get("source_artifact_id")
                if artifact_id:
                    bound_sources[index]["source_artifact_id"] = artifact_id
                    bound_sources[index]["fingerprint"] = loaded.baseline.fingerprint.sha256
                    if source_content_identity(source) != loaded.baseline.fingerprint.namespace.value:
                        legacy = dict(source.get("legacy") or {})
                        legacy["namespace"] = loaded.baseline.fingerprint.namespace.value
                        bound_sources[index]["legacy"] = legacy
        except (OSError, DomainError) as exc:
            diagnostic = source_recovery_diagnostic(source, exc)
            failures[source.get("source_id")] = diagnostic
            warnings.append(diagnostic)
    baselines = tuple(item.baseline if isinstance(item, PreparedProjectSource) else item for item in loaded_sources)
    hydrations = tuple(
        item.hydration
        for item in loaded_sources
        if isinstance(item, PreparedProjectSource) and item.hydration is not None
    )
    bound = replace(
        project, envelope=replace(project.envelope, data={**project.envelope.data, "sources": bound_sources})
    )
    missing = missing_inputs_for(bound, snapshot, hydrations, failures)
    for item in missing:
        if item.diagnostic not in warnings:
            warnings.append(item.diagnostic)
    return LoadedProjectSourceInputs(tuple(bound_sources), baselines, hydrations, missing, tuple(warnings))
