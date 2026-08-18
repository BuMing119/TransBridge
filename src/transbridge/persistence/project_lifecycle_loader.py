"""V2 repository adapter for isolated lifecycle candidate preparation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.projects.models import ActiveProject, TransitionTarget

from .v2.ids import ProjectRef, VariantRef
from .v2.models import LoadedRecord, ProjectDto, VariantDto
from .v2.variant import (
    SourceBaseline,
    VariantAggregate,
    VariantMaterializer,
    VariantSnapshot,
)


class ProjectLoadPort(Protocol):
    def load(self, ref: ProjectRef): ...


class VariantLoadPort(Protocol):
    def load(self, ref: VariantRef): ...


BaselineProvider = Callable[[ProjectDto, VariantRef, RequestContext], tuple[SourceBaseline, ...]]
SnapshotLoader = Callable[[str, VariantRef, RequestContext], VariantSnapshot]


class V2ProjectCandidateLoader:
    def __init__(
        self,
        projects: ProjectLoadPort,
        variants: VariantLoadPort,
        baseline_provider: BaselineProvider,
        *,
        snapshot_loader: SnapshotLoader | None = None,
    ) -> None:
        self._projects = projects
        self._variants = variants
        self._baseline_provider = baseline_provider
        self._snapshot_loader = snapshot_loader

    def prepare_candidate(self, target: TransitionTarget, context: RequestContext) -> ActiveProject:
        if target.project_ref is None:
            raise ValueError("candidate loader cannot prepare a close transition")
        project = self._load_project(target.project_ref)
        if target.variant_ref is None:
            return ActiveProject(
                project=project,
                variant=None,
                formal_variant_ref=None,
                persisted_project_revision=project.envelope.revision,
                persisted_variant_revision=None,
            )
        if target.variant_ref.identity.value not in project.envelope.data["variant_ids"]:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "VARIANT_NOT_DECLARED_BY_PROJECT",
                "The target Variant is not declared by the target Project.",
            )
        stored = self._load_variant(target, context)
        baselines = self._baseline_provider(project, target.variant_ref, context)
        materialized = self._materialize(stored, baselines, context)
        persisted_variant_revision = stored.revision if materialized == stored else None
        return ActiveProject(
            project=project,
            variant=VariantAggregate(materialized),
            formal_variant_ref=target.variant_ref,
            persisted_project_revision=project.envelope.revision,
            persisted_variant_revision=persisted_variant_revision,
            source_ref=target.snapshot_ref,
        )

    def _load_project(self, ref: ProjectRef) -> ProjectDto:
        result = self._projects.load(ref)
        if not isinstance(result, LoadedRecord) or not isinstance(result.value, ProjectDto):
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "PROJECT_RECORD_UNAVAILABLE",
                "The target Project record is unavailable or read-only.",
            )
        return result.value

    def _load_variant(self, target: TransitionTarget, context: RequestContext) -> VariantSnapshot:
        assert target.variant_ref is not None
        if target.snapshot_ref is not None:
            if self._snapshot_loader is None:
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SNAPSHOT_LOADER_UNAVAILABLE",
                    "Snapshot loading is unavailable for this runtime.",
                )
            snapshot = self._snapshot_loader(target.snapshot_ref, target.variant_ref, context)
            if snapshot.ref != target.variant_ref:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "SNAPSHOT_FORMAL_VARIANT_MISMATCH",
                    "The snapshot does not belong to the formal target Variant.",
                )
            return snapshot
        result = self._variants.load(target.variant_ref)
        if not isinstance(result, LoadedRecord) or not isinstance(result.value, VariantDto):
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "VARIANT_RECORD_UNAVAILABLE",
                "The target Variant record is unavailable or read-only.",
            )
        return VariantSnapshot.from_dto(result.value, target.variant_ref)

    def _materialize(
        self,
        stored: VariantSnapshot,
        baselines: tuple[SourceBaseline, ...],
        context: RequestContext,
    ) -> VariantSnapshot:
        baseline_entries = tuple(entry for baseline in baselines for entry in baseline.entries)
        baseline_fingerprints = tuple(baseline.fingerprint for baseline in baselines)
        temporary = VariantAggregate(VariantSnapshot(stored.ref, baseline_fingerprints, baseline_entries))
        result = VariantMaterializer().materialize(stored, baselines, temporary, context)
        if not result.committed:
            code = result.diagnostics[-1].code if result.diagnostics else "VARIANT_MATERIALIZATION_FAILED"
            category = ErrorCategory.CONFLICT if result.migration_plan is not None else ErrorCategory.PREREQUISITE
            raise DomainError(
                category,
                code,
                "The target Variant could not be materialized against the current source baseline.",
            )
        projected = temporary.snapshot()
        return VariantSnapshot(
            stored.ref,
            projected.source_fingerprints,
            projected.entries,
            stored.revision,
            stored.label_library,
        )


__all__ = [
    "BaselineProvider",
    "ProjectLoadPort",
    "SnapshotLoader",
    "V2ProjectCandidateLoader",
    "VariantLoadPort",
]
