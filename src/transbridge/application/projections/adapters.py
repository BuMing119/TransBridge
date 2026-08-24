"""Projection adapters for Project/Variant and Session aggregates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transbridge.application.projects import (
    ActiveProject,
    LifecycleEvent,
    ProjectLifecycleService,
    project_paratranz_binding,
    variant_catalog,
)
from transbridge.application.sessions import SessionLifecycleService, SessionSnapshot

from .models import ProjectionSnapshot
from .store import ProjectionStore


def project_projection(active: ActiveProject | None) -> ProjectionSnapshot | None:
    if active is None:
        return None
    summary = active.summary()
    variant = None if active.variant is None else active.variant.snapshot()
    values: dict[str, Any] = dict(summary)
    variants = variant_catalog(active.project)
    paratranz_binding = project_paratranz_binding(active.project)
    values.update({
        "project_name": str(active.project.envelope.data.get("name", "")),
        "sources": [dict(value) for value in active.project.envelope.data.get("sources", ())],
        "active_variant_id": active.project.envelope.data.get("active_variant_id"),
        "variants": [item.to_dict() for item in variants],
        "entries": [] if variant is None else [entry.to_dict() for entry in variant.entries],
        "label_library": ({} if variant is None else variant.to_dto().envelope.data.get("label_library", {})),
        "paratranz_binding": None if paratranz_binding is None else paratranz_binding.to_dict(),
    })
    aggregate_revision = active.project.envelope.revision
    persisted_revision = active.persisted_project_revision
    if variant is not None:
        aggregate_revision += variant.revision
        persisted_revision += active.persisted_variant_revision or 0
    return ProjectionSnapshot(
        f"project:{active.project_ref.identity.value}",
        aggregate_revision,
        min(persisted_revision, aggregate_revision),
        values,
    )


def session_projection(
    snapshot: SessionSnapshot | None,
    *,
    persisted_revision: int | None = None,
) -> ProjectionSnapshot | None:
    if snapshot is None:
        return None
    values = {
        "session_id": snapshot.ref.identity.value,
        "name": snapshot.name,
        "messages": list(snapshot.visible_messages()),
        "backend_history": list(snapshot.backend_messages()),
        "backend_summary": snapshot.backend_summary,
        "controller": snapshot.controller.to_dict(),
        "project_id": None if snapshot.project_id is None else snapshot.project_id.value,
        "variant_id": None if snapshot.variant_id is None else snapshot.variant_id.value,
        "pending_approvals": [item.to_dict() for item in snapshot.approvals],
        "job_refs": [item.to_dict() for item in snapshot.jobs],
        "recovery": snapshot.recovery.value,
        "degradation_reasons": list(snapshot.degradation_reasons),
    }
    return ProjectionSnapshot(
        f"session:{snapshot.ref.identity.value}",
        snapshot.revision,
        snapshot.revision if persisted_revision is None else persisted_revision,
        values,
    )


@dataclass(slots=True)
class ProjectProjectionPublisher:
    store: ProjectionStore
    lifecycle: ProjectLifecycleService | None = None

    def bind(self, lifecycle: ProjectLifecycleService) -> None:
        self.lifecycle = lifecycle
        self.rebuild()

    def __call__(self, event: LifecycleEvent) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        active = None if self.lifecycle is None else self.lifecycle.active
        self.store.rebuild(project_projection(active))


@dataclass(slots=True)
class SessionProjectionPublisher:
    store: ProjectionStore
    lifecycle: SessionLifecycleService | None = None

    def bind(self, lifecycle: SessionLifecycleService) -> None:
        self.lifecycle = lifecycle
        self.rebuild()

    def __call__(self, snapshot: SessionSnapshot | None) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        active = None if self.lifecycle is None else self.lifecycle.active
        snapshot = None if active is None else active.aggregate.snapshot()
        persisted = None if active is None else active.persisted_revision
        self.store.rebuild(session_projection(snapshot, persisted_revision=persisted))


__all__ = [
    "ProjectProjectionPublisher",
    "SessionProjectionPublisher",
    "project_projection",
    "session_projection",
]
