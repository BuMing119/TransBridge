"""Display-safe Variant catalog helpers for V2 Projects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from transbridge.persistence.v2.ids import VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope


@dataclass(frozen=True, slots=True)
class VariantDescriptor:
    variant_id: str
    name: str
    active: bool = False

    def to_dict(self) -> dict[str, str | bool]:
        return {"id": self.variant_id, "name": self.name, "active": self.active}


def variant_catalog(project: ProjectDto) -> tuple[VariantDescriptor, ...]:
    """Return stable display metadata without treating opaque IDs as names."""

    data = project.envelope.data
    variant_ids = tuple(str(value) for value in data.get("variant_ids", ()))
    explicit = data.get("variant_names")
    names_by_id = {str(key): str(value) for key, value in explicit.items()} if isinstance(explicit, dict) else {}
    legacy = data.get("legacy")
    legacy_map = legacy.get("variant_name_map") if isinstance(legacy, dict) else None
    if isinstance(legacy_map, dict):
        for name, variant_id in legacy_map.items():
            names_by_id.setdefault(str(variant_id), str(name))

    active_id = data.get("active_variant_id")
    used: set[str] = set()
    descriptors: list[VariantDescriptor] = []
    for index, variant_id in enumerate(variant_ids):
        fallback = "默认" if len(variant_ids) == 1 else f"版本 {index + 1}"
        name = _unique_display_name(names_by_id.get(variant_id, fallback), used)
        used.add(name.casefold())
        descriptors.append(VariantDescriptor(variant_id, name, variant_id == active_id))
    return tuple(descriptors)


def project_with_added_variant(project: ProjectDto, variant_ref: VariantRef, name: str) -> ProjectDto:
    if variant_ref.project_id.value != project.envelope.identity:
        raise ValueError("Variant must belong to the Project")
    display_name = _validate_display_name(name)
    catalog = variant_catalog(project)
    if variant_ref.identity.value in {item.variant_id for item in catalog}:
        raise ValueError("Variant ID already exists in the Project")
    if display_name.casefold() in {item.name.casefold() for item in catalog}:
        raise ValueError("Variant display name already exists in the Project")

    data = deepcopy(project.envelope.data)
    data["variant_ids"] = [*data.get("variant_ids", ()), variant_ref.identity.value]
    data["variant_names"] = {
        **{item.variant_id: item.name for item in catalog},
        variant_ref.identity.value: display_name,
    }
    return _replace_project_data(project, data)


def project_without_variant(project: ProjectDto, variant_id: str) -> ProjectDto:
    catalog = variant_catalog(project)
    if variant_id not in {item.variant_id for item in catalog}:
        raise ValueError("Variant is not declared by the Project")
    if len(catalog) <= 1:
        raise ValueError("A Project must retain at least one Variant")
    if project.envelope.data.get("active_variant_id") == variant_id:
        raise ValueError("The active Variant must be switched before deletion")

    data = deepcopy(project.envelope.data)
    data["variant_ids"] = [item.variant_id for item in catalog if item.variant_id != variant_id]
    data["variant_names"] = {item.variant_id: item.name for item in catalog if item.variant_id != variant_id}
    return _replace_project_data(project, data)


def _replace_project_data(project: ProjectDto, data: dict) -> ProjectDto:
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


def _validate_display_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 80 or any(character in name for character in "\r\n\t"):
        raise ValueError("Variant display name must be 1-80 printable characters")
    return name


def _unique_display_name(value: str, used: set[str]) -> str:
    base = _validate_display_name(value)
    if base.casefold() not in used:
        return base
    suffix = 2
    while f"{base} ({suffix})".casefold() in used:
        suffix += 1
    return f"{base} ({suffix})"


__all__ = [
    "VariantDescriptor",
    "project_with_added_variant",
    "project_without_variant",
    "variant_catalog",
]
