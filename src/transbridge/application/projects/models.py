"""Immutable contracts for two-phase Project/Variant lifecycle changes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict

from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import VariantAggregate, VariantSnapshot


class DirtyDecision(StrEnum):
    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class TransitionTarget:
    project_ref: ProjectRef | None
    variant_ref: VariantRef | None = None
    snapshot_ref: str | None = None

    def __post_init__(self) -> None:
        if self.project_ref is None:
            if self.variant_ref is not None or self.snapshot_ref is not None:
                raise ValueError("a close target cannot include a Variant or snapshot")
            return
        if self.variant_ref is not None and self.variant_ref.project_id != self.project_ref.identity:
            raise ValueError("target Variant must belong to the target Project")
        if self.snapshot_ref is not None and self.variant_ref is None:
            raise ValueError("snapshot materialization requires a formal Variant reference")
        if self.snapshot_ref is not None and not self.snapshot_ref.strip():
            raise ValueError("snapshot_ref must not be empty")

    @classmethod
    def close(cls) -> TransitionTarget:
        return cls(None)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "project_id": None if self.project_ref is None else self.project_ref.identity.value,
            "variant_id": None if self.variant_ref is None else self.variant_ref.identity.value,
            "snapshot_ref": self.snapshot_ref,
        }


@dataclass(frozen=True, slots=True)
class LifecycleLease:
    lease_id: str
    owner_id: str

    def __post_init__(self) -> None:
        if not self.lease_id.strip() or not self.owner_id.strip():
            raise ValueError("lifecycle lease identity and owner must not be empty")


@dataclass(frozen=True, slots=True)
class ActiveProject:
    project: ProjectDto
    variant: VariantAggregate | None
    formal_variant_ref: VariantRef | None
    persisted_project_revision: int
    persisted_variant_revision: int | None
    source_ref: str | None = None
    leases: tuple[LifecycleLease, ...] = ()

    def __post_init__(self) -> None:
        project_ref = self.project_ref
        if self.formal_variant_ref is not None and self.formal_variant_ref.project_id != project_ref.identity:
            raise ValueError("active Variant must belong to the active Project")
        if (self.variant is None) != (self.formal_variant_ref is None):
            raise ValueError("active Variant aggregate and formal reference must be present together")
        if self.variant is not None and self.variant.ref != self.formal_variant_ref:
            raise ValueError("active Variant aggregate does not match its formal reference")
        if self.persisted_variant_revision is not None and self.persisted_variant_revision < 0:
            raise ValueError("persisted Variant revision must not be negative")
        if self.variant is None and self.persisted_variant_revision is not None:
            raise ValueError("a Project without an active Variant cannot have a persisted Variant revision")
        if self.persisted_project_revision < 0:
            raise ValueError("persisted Project revision must not be negative")

    @property
    def project_ref(self) -> ProjectRef:
        return ProjectRef(ProjectId(self.project.envelope.identity))

    @property
    def dirty(self) -> bool:
        if self.persisted_project_revision != self.project.envelope.revision:
            return True
        if self.variant is None:
            return False
        return self.persisted_variant_revision != self.variant.revision

    def summary(self) -> dict[str, Any]:
        return {
            "project_id": self.project_ref.identity.value,
            "project_revision": self.project.envelope.revision,
            "persisted_project_revision": self.persisted_project_revision,
            "variant_id": None if self.formal_variant_ref is None else self.formal_variant_ref.identity.value,
            "variant_revision": None if self.variant is None else self.variant.revision,
            "persisted_variant_revision": self.persisted_variant_revision,
            "source_ref": self.source_ref,
            "dirty": self.dirty,
        }


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    project_ref: ProjectRef
    formal_variant_ref: VariantRef
    variant: VariantSnapshot
    name: str


@dataclass(frozen=True, slots=True)
class LifecycleSave:
    project: ProjectDto
    formal_variant_ref: VariantRef | None
    variant: VariantSnapshot | None
    expected_persisted_project_revision: int
    expected_persisted_revision: int | None

    @classmethod
    def capture(cls, active: ActiveProject) -> LifecycleSave:
        return cls(
            _clone_project(active.project),
            active.formal_variant_ref,
            None if active.variant is None else active.variant.snapshot(),
            active.persisted_project_revision,
            active.persisted_variant_revision,
        )


@dataclass(frozen=True, slots=True)
class LifecycleActivation:
    old_project_ref: ProjectRef | None
    old_project_revision: int | None
    old_variant_ref: VariantRef | None
    old_variant_revision: int | None
    candidate_project: ProjectDto | None
    candidate_variant_ref: VariantRef | None
    candidate_variant: VariantSnapshot | None
    source_ref: str | None

    @classmethod
    def capture(
        cls,
        old: ActiveProject | None,
        candidate: ActiveProject | None,
    ) -> LifecycleActivation:
        return cls(
            None if old is None else old.project_ref,
            None if old is None else old.project.envelope.revision,
            None if old is None else old.formal_variant_ref,
            None if old is None or old.variant is None else old.variant.revision,
            None if candidate is None else _clone_project(candidate.project),
            None if candidate is None else candidate.formal_variant_ref,
            None if candidate is None or candidate.variant is None else candidate.variant.snapshot(),
            None if candidate is None else candidate.source_ref,
        )


class PreparedTransition(TypedDict):
    token: str
    owner_id: str
    expected_generation: int
    old: dict[str, Any] | None
    candidate: dict[str, Any] | None
    target: dict[str, str | None]
    leases: list[str]


class ExportRevisionLease(TypedDict):
    token: str
    owner_id: str
    generation: int
    project_id: str
    variant_id: str
    variant_revision: int


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    name: str
    generation: int
    old: dict[str, Any] | None
    current: dict[str, Any] | None


def project_with_active_variant(project: ProjectDto, variant_ref: VariantRef | None) -> ProjectDto:
    data = deepcopy(project.envelope.data)
    active_id = None if variant_ref is None else variant_ref.identity.value
    variant_ids = tuple(str(value) for value in data["variant_ids"])
    if active_id is not None and active_id not in variant_ids:
        raise ValueError("active Variant must be declared by the Project")
    data["active_variant_id"] = active_id
    envelope = project.envelope
    return ProjectDto(
        SchemaEnvelope(
            envelope.schema_version,
            envelope.entity_type,
            envelope.identity,
            envelope.revision + 1,
            data,
        )
    )


def _clone_project(project: ProjectDto) -> ProjectDto:
    envelope = project.envelope
    return ProjectDto(
        SchemaEnvelope(
            envelope.schema_version,
            envelope.entity_type,
            envelope.identity,
            envelope.revision,
            deepcopy(envelope.data),
        )
    )


__all__ = [
    "ActiveProject",
    "DirtyDecision",
    "ExportRevisionLease",
    "LifecycleActivation",
    "LifecycleEvent",
    "LifecycleLease",
    "LifecycleSave",
    "LifecycleSnapshot",
    "PreparedTransition",
    "TransitionTarget",
    "project_with_active_variant",
]
