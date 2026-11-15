# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""User-facing source replacement previews, independent of Qt."""

from dataclasses import dataclass

from transbridge.application.io import FormatId
from transbridge.application.projects.storage_contracts import ProjectReadToken


@dataclass(frozen=True, slots=True)
class SourceUpdateTarget:
    source_id: str
    location: str
    format_id: FormatId
    label: str


@dataclass(frozen=True, slots=True)
class SourceUpdatePreview:
    token: str
    source_name: str
    replacement_path: str
    variant_count: int
    added: int
    removed: int
    unchanged: int
    changed: int
    unverified: int
    reordered: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceUpdateDifference:
    source_name: str
    current_path: str
    replacement_path: str
    added: int
    removed: int
    unchanged: int
    changed: int
    unverified: int
    reordered: int


@dataclass(frozen=True, slots=True)
class BatchSourceUpdatePreview:
    token: str
    variant_count: int
    differences: tuple[SourceUpdateDifference, ...]


@dataclass(frozen=True, slots=True)
class SourceUpdateResult:
    project_path: str
    backup_path: str
    active_token: ProjectReadToken | None = None
