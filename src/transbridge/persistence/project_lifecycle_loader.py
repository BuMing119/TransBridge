"""V2 repository adapter for isolated lifecycle candidate preparation."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Protocol

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.projects.models import ActiveProject, TransitionTarget
from transbridge.application.projects.source_inputs import missing_inputs_for

from .prepared_project_read import (
    prepared_missing_inputs,
    prepared_project_sources,
    prepared_source_hydrations,
    prepared_variant,
    verify_prepared_project,
)
from .v2.ids import ProjectRef, VariantRef
from .v2.models import LoadedRecord, ProjectDto, VariantDto
from .v2.variant import (
    SourceBaseline,
    VariantAggregate,
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
        source_inputs_loader=None,
    ) -> None:
        self._projects = projects
        self._variants = variants
        self._baseline_provider = baseline_provider
        self._snapshot_loader = snapshot_loader
        self._source_inputs_loader = source_inputs_loader

    def prepare_candidate(self, target: TransitionTarget, context: RequestContext) -> ActiveProject:
        if target.project_ref is None:
            raise ValueError("candidate loader cannot prepare a close transition")
        project_record = self._projects.load(target.project_ref)
        if not isinstance(project_record, LoadedRecord) or not isinstance(project_record.value, ProjectDto):
            raise DomainError(
                ErrorCategory.PREREQUISITE, "PROJECT_RECORD_UNAVAILABLE", "Project record is unavailable."
            )
        project = project_record.value
        verify_prepared_project(self._projects, project_record)
        persisted_project_revision = project.envelope.revision
        bound_sources = prepared_project_sources()
        if bound_sources is not None and list(bound_sources) != project.envelope.data["sources"]:
            data = deepcopy(project.envelope.data)
            data["sources"] = list(deepcopy(bound_sources))
            project = ProjectDto(replace(project.envelope, revision=persisted_project_revision + 1, data=data))
        if target.variant_ref is None:
            return ActiveProject(
                project=project,
                variant=None,
                formal_variant_ref=None,
                persisted_project_revision=persisted_project_revision,
                persisted_variant_revision=None,
                source_project_hash=project_record.source_hash,
            )
        if target.variant_ref.identity.value not in project.envelope.data["variant_ids"]:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "VARIANT_NOT_DECLARED_BY_PROJECT",
                "The target Variant is not declared by the target Project.",
            )
        stored, formal_record = self._load_variant(target, context)
        missing_inputs = prepared_missing_inputs()
        hydrations = prepared_source_hydrations() or ()
        baselines = None
        if missing_inputs is None:
            missing_inputs = ()
            if self._source_inputs_loader is not None:
                inputs = self._source_inputs_loader(project, stored, context)
                missing_inputs = inputs.missing_inputs
                hydrations = inputs.hydrations
                baselines = inputs.baselines
                if list(inputs.sources) != project.envelope.data.get("sources", []):
                    data = {**project.envelope.data, "sources": list(inputs.sources)}
                    project = ProjectDto(replace(project.envelope, revision=persisted_project_revision + 1, data=data))
        if baselines is None:
            baselines = self._baseline_provider(project, target.variant_ref, context)
        materialized = self._materialize(stored, baselines)
        missing_inputs = missing_inputs_for(
            project,
            materialized,
            hydrations,
            {item.source_id or item.namespace: item.diagnostic for item in missing_inputs},
        )
        persisted_variant_revision = stored.revision if materialized == stored else None
        return ActiveProject(
            project=project,
            variant=VariantAggregate(materialized),
            formal_variant_ref=target.variant_ref,
            persisted_project_revision=persisted_project_revision,
            persisted_variant_revision=persisted_variant_revision,
            source_ref=target.snapshot_ref,
            source_project_hash=project_record.source_hash,
            source_variant_hash=formal_record.source_hash,
            source_variant_revision=formal_record.value.envelope.revision,
            missing_inputs=missing_inputs,
        )

    def _load_variant(self, target: TransitionTarget, context: RequestContext) -> tuple[VariantSnapshot, LoadedRecord]:
        assert target.variant_ref is not None
        snapshot = None
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
        result = self._variants.load(target.variant_ref)
        if not isinstance(result, LoadedRecord) or not isinstance(result.value, VariantDto):
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "VARIANT_RECORD_UNAVAILABLE",
                "The target Variant record is unavailable or read-only.",
            )
        # Snapshot normalization may add optional fields absent from older
        # records. Keep the exact loaded record's digest for commit-time stale
        # checks instead of comparing its DTO with a normalized round-trip.
        if snapshot is None:
            snapshot = prepared_variant(self._variants, result)
            if snapshot is None:
                snapshot = VariantSnapshot.from_dto(result.value, target.variant_ref)
        return snapshot, result

    def _materialize(
        self,
        stored: VariantSnapshot,
        baselines: tuple[SourceBaseline, ...],
    ) -> VariantSnapshot:
        """Add verified defaults without dropping any saved state or changing identity."""
        fingerprints = {item.namespace: item for item in stored.source_fingerprints}
        entries = {entry.entry_key: entry for entry in stored.entries}
        seen = set()
        for baseline in baselines:
            namespace = baseline.fingerprint.namespace
            if namespace in seen:
                raise ValueError("duplicate source baseline namespace")
            seen.add(namespace)
            previous = fingerprints.get(namespace)
            if previous is not None and previous != baseline.fingerprint:
                continue
            fingerprints[namespace] = baseline.fingerprint
            for default in baseline.entries:
                saved = entries.get(default.entry_key)
                if saved is None:
                    entries[default.entry_key] = default
                elif not saved.tombstone:
                    inferred = set(saved.inferred_fields) & {"stage", "provenance", "revision", "external_refs"}
                    inferred = {field for field in inferred if getattr(saved, field) != getattr(default, field)}
                    if inferred:
                        entries[default.entry_key] = replace(
                            saved,
                            **{field: getattr(default, field) for field in inferred},
                            inferred_fields=tuple(field for field in saved.inferred_fields if field not in inferred),
                        )
        return VariantSnapshot(
            stored.ref,
            tuple(fingerprints.values()),
            tuple(entries.values()),
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
