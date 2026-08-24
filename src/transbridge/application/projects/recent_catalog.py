"""Read-only Project catalog contract for start-center projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from transbridge.application.contracts import Diagnostic


@dataclass(frozen=True, slots=True)
class ProjectCatalogEntry:
    """One display-safe Project record discovered from the V2 catalog."""

    project_id: str
    name: str
    path: str
    active: bool
    available: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id or not self.name or not self.path:
            raise ValueError("Project catalog entry identity, name, and path must not be empty")
        if self.available and self.reason is not None:
            raise ValueError("Available Project catalog entries must not carry an unavailable reason")
        if not self.available and not self.reason:
            raise ValueError("Unavailable Project catalog entries require a safe reason")

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "path": self.path,
            "active": self.active,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProjectCatalogSnapshot:
    """Immutable recent-Project list plus caller-safe diagnostics."""

    projects: tuple[ProjectCatalogEntry, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "projects": [item.to_dict() for item in self.projects],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@runtime_checkable
class ProjectCatalogQuery(Protocol):
    """Narrow query used by UI entrypoints; implementations must be read-only."""

    def list_projects(self) -> ProjectCatalogSnapshot: ...


__all__ = ["ProjectCatalogEntry", "ProjectCatalogQuery", "ProjectCatalogSnapshot"]
