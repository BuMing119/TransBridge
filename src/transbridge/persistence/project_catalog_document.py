"""Strict schema-1 Project catalog document helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transbridge.persistence.v2.ids import ProjectId
from transbridge.persistence.v2.schema import parse_json_bytes


@dataclass(frozen=True, slots=True)
class ProjectCatalogRecord:
    project_id: str
    name: str
    name_key: str

    def __post_init__(self) -> None:
        ProjectId(self.project_id)
        canonical_name = project_display_name(self.name)
        if canonical_name != self.name:
            raise ValueError("Project catalog display name must be canonical")
        if project_name_key(canonical_name) != self.name_key:
            raise ValueError("Project catalog name key does not match its display name")


def parse_project_catalog(raw: bytes) -> tuple[ProjectCatalogRecord, ...]:
    document = parse_json_bytes(raw)
    projects = document.get("projects")
    if document.get("schema_version") != 1 or not isinstance(projects, dict):
        raise ValueError("invalid Project catalog envelope")

    records: list[ProjectCatalogRecord] = []
    names: set[str] = set()
    for raw_id, raw_entry in projects.items():
        if not isinstance(raw_id, str) or not isinstance(raw_entry, dict):
            raise ValueError("invalid Project catalog entry")
        project_id = ProjectId(raw_id).value
        name = project_display_name(raw_entry.get("name"))
        expected_name_key = project_name_key(name)
        raw_name_key = raw_entry.get("name_key", expected_name_key)
        if not isinstance(raw_name_key, str) or raw_name_key != expected_name_key:
            raise ValueError("invalid Project catalog name key")
        if expected_name_key in names:
            raise ValueError("duplicate Project catalog name")
        names.add(expected_name_key)
        records.append(ProjectCatalogRecord(project_id, name, expected_name_key))
    return tuple(sorted(records, key=lambda record: record.project_id))


def build_project_catalog(records: tuple[ProjectCatalogRecord, ...]) -> dict[str, Any]:
    names: set[str] = set()
    projects: dict[str, dict[str, str]] = {}
    for record in sorted(records, key=lambda item: item.project_id):
        if record.name_key in names:
            raise ValueError("duplicate Project catalog name")
        names.add(record.name_key)
        projects[record.project_id] = {"name": record.name, "name_key": record.name_key}
    return {"schema_version": 1, "projects": projects}


def project_display_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Project catalog name must be text")
    name = value.strip()
    if not name or len(name) > 80 or any(character in name for character in "\r\n\t"):
        raise ValueError("Project catalog name is invalid")
    return name


def project_name_key(name: str) -> str:
    return project_display_name(name).casefold()


__all__ = [
    "ProjectCatalogRecord",
    "build_project_catalog",
    "parse_project_catalog",
    "project_display_name",
    "project_name_key",
]
