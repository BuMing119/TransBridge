"""Validated Project source registrations and explicit relationship graphs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
import re
from typing import Any

from transbridge.application.io import FormatId

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SourceKind(StrEnum):
    PLUGIN = "plugin"
    TRANSLATION = "translation"
    LOCALIZED_STRINGS = "localized_strings"
    BILINGUAL = "bilingual"
    OTHER = "other"


class BilingualCapability(StrEnum):
    NONE = "none"
    SELF_CONTAINED = "self_contained"
    REQUIRES_RELATION = "requires_relation"


class SourceRelationKind(StrEnum):
    TRANSLATION_FOR = "translation_for"
    LOCALIZED_MEMBER_OF = "localized_member_of"


@dataclass(frozen=True, order=True, slots=True)
class SourceRegistration:
    source_id: str
    enabled: bool
    format_id: FormatId
    location: str
    kind: SourceKind
    bilingual_capability: BilingualCapability
    fingerprint: str | None = None
    display_name: str | None = None
    plugin_scope: str | None = None
    format_options: tuple[tuple[str, Any], ...] = ()
    legacy: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not _valid_id(self.source_id):
            raise ValueError("source_id must be a canonical path-independent identifier")
        if not isinstance(self.enabled, bool):
            raise TypeError("source enabled state must be boolean")
        try:
            format_id = self.format_id if isinstance(self.format_id, FormatId) else FormatId(self.format_id)
            kind = self.kind if isinstance(self.kind, SourceKind) else SourceKind(self.kind)
            bilingual = (
                self.bilingual_capability
                if isinstance(self.bilingual_capability, BilingualCapability)
                else BilingualCapability(self.bilingual_capability)
            )
        except ValueError as exc:
            raise ValueError("source registration contains an unsupported enum value") from exc
        location = normalize_source_location(self.location)
        if self.fingerprint is not None and not _SHA256.fullmatch(self.fingerprint):
            raise ValueError("source fingerprint must be a lowercase SHA-256 digest")
        options = _canonical_items(self.format_options, "format option")
        legacy = _canonical_items(self.legacy, "legacy field")
        display_name = _optional_text(self.display_name, "display_name")
        plugin_scope = _optional_text(self.plugin_scope, "plugin_scope")
        object.__setattr__(self, "format_id", format_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "bilingual_capability", bilingual)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "plugin_scope", plugin_scope)
        object.__setattr__(self, "format_options", options)
        object.__setattr__(self, "legacy", legacy)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": self.source_id,
            "enabled": self.enabled,
            "format_id": self.format_id.value,
            "location": self.location,
            "kind": self.kind.value,
            "bilingual_capability": self.bilingual_capability.value,
            "format_options": dict(self.format_options),
        }
        for key, value in (
            ("fingerprint", self.fingerprint),
            ("display_name", self.display_name),
            ("plugin_scope", self.plugin_scope),
        ):
            if value is not None:
                result[key] = value
        if self.legacy:
            result["legacy"] = dict(self.legacy)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceRegistration:
        return cls(
            source_id=str(value["source_id"]),
            enabled=value.get("enabled", True),
            format_id=FormatId(str(value["format_id"])),
            location=str(value["location"]),
            kind=SourceKind(str(value["kind"])),
            bilingual_capability=BilingualCapability(str(value["bilingual_capability"])),
            fingerprint=value.get("fingerprint"),
            display_name=value.get("display_name"),
            plugin_scope=value.get("plugin_scope"),
            format_options=tuple((value.get("format_options") or {}).items()),
            legacy=tuple((value.get("legacy") or {}).items()),
        )


@dataclass(frozen=True, order=True, slots=True)
class SourceRelation:
    relation_id: str
    kind: SourceRelationKind
    from_source_id: str
    to_source_id: str
    alignment_policy: str = "entry_key"
    alignment_version: str = "1"

    def __post_init__(self) -> None:
        if not _valid_id(self.relation_id):
            raise ValueError("relation_id must be a canonical path-independent identifier")
        if not _valid_id(self.from_source_id) or not _valid_id(self.to_source_id):
            raise ValueError("source relation references must use canonical source identifiers")
        if self.from_source_id == self.to_source_id:
            raise ValueError("source relations must not be self-referential")
        kind = self.kind if isinstance(self.kind, SourceRelationKind) else SourceRelationKind(self.kind)
        policy = _required_text(self.alignment_policy, "alignment_policy")
        version = _required_text(self.alignment_version, "alignment_version")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "alignment_policy", policy)
        object.__setattr__(self, "alignment_version", version)

    def to_dict(self) -> dict[str, str]:
        return {
            "relation_id": self.relation_id,
            "kind": self.kind.value,
            "from_source_id": self.from_source_id,
            "to_source_id": self.to_source_id,
            "alignment_policy": self.alignment_policy,
            "alignment_version": self.alignment_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SourceRelation:
        return cls(
            relation_id=str(value["relation_id"]),
            kind=SourceRelationKind(str(value["kind"])),
            from_source_id=str(value["from_source_id"]),
            to_source_id=str(value["to_source_id"]),
            alignment_policy=str(value["alignment_policy"]),
            alignment_version=str(value["alignment_version"]),
        )


@dataclass(frozen=True, slots=True)
class SourceRegistrySnapshot:
    sources: tuple[SourceRegistration, ...]
    relations: tuple[SourceRelation, ...] = ()
    diagnostics: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        sources = tuple(sorted(self.sources, key=lambda item: item.source_id))
        relations = tuple(sorted(self.relations, key=lambda item: item.relation_id))
        source_ids = [item.source_id for item in sources]
        relation_ids = [item.relation_id for item in relations]
        locations = [os.path.normcase(item.location) for item in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Project source identifiers must be unique")
        if len(locations) != len(set(locations)):
            raise ValueError("Project source locations must be unique")
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("Project source relation identifiers must be unique")
        known = set(source_ids)
        edges: set[tuple[str, str, SourceRelationKind]] = set()
        for relation in relations:
            if relation.from_source_id not in known or relation.to_source_id not in known:
                raise ValueError("Project source relation contains a dangling reference")
            edge = (relation.from_source_id, relation.to_source_id, relation.kind)
            if edge in edges:
                raise ValueError("Project source relations must not duplicate an edge")
            edges.add(edge)
        _reject_cycles(relations)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "relations", relations)
        object.__setattr__(self, "diagnostics", tuple(sorted(self.diagnostics)))

    def to_project_data(self) -> dict[str, Any]:
        return {
            "sources": [item.to_dict() for item in self.sources],
            "source_relations": [item.to_dict() for item in self.relations],
            "source_registry_diagnostics": [
                {"code": code, "source_id": source_id} for code, source_id in self.diagnostics
            ],
        }

    @classmethod
    def from_project_data(cls, data: Mapping[str, Any]) -> SourceRegistrySnapshot:
        diagnostics = tuple(
            (str(item["code"]), str(item.get("source_id", ""))) for item in data.get("source_registry_diagnostics", ())
        )
        return cls(
            tuple(SourceRegistration.from_dict(item) for item in data.get("sources", ())),
            tuple(SourceRelation.from_dict(item) for item in data.get("source_relations", ())),
            diagnostics,
        )


def migrate_legacy_source_registry(
    project_id: str,
    values: Iterable[Mapping[str, Any]],
) -> SourceRegistrySnapshot:
    """Map V2 source descriptors without using content fingerprints as identity."""

    registrations: list[SourceRegistration] = []
    roles: dict[str, str] = {}
    for value in values:
        location = str(value.get("location") or value.get("path") or "").strip()
        if not location:
            raise ValueError("legacy Project source requires a location")
        format_id = _legacy_format_id(value, location)
        role = str(value.get("role") or "").strip().lower()
        source_id = stable_source_id(project_id, location, format_id)
        kind, bilingual = _source_shape(format_id)
        legacy = {
            "role": role or None,
            "source_id": value.get("source_id"),
            "namespace": value.get("namespace") or value.get("key"),
            "path": value.get("path"),
        }
        registrations.append(
            SourceRegistration(
                source_id=source_id,
                enabled=bool(value.get("enabled", True)),
                format_id=format_id,
                location=location,
                kind=kind,
                bilingual_capability=bilingual,
                fingerprint=value.get("fingerprint"),
                display_name=value.get("display_name"),
                plugin_scope=value.get("plugin_scope"),
                format_options=tuple((value.get("format_options") or value.get("options") or {}).items()),
                legacy=tuple((key, item) for key, item in legacy.items() if item is not None),
            )
        )
        if role:
            roles[source_id] = role

    primary = [item.source_id for item in registrations if roles.get(item.source_id) == "primary"]
    relations: list[SourceRelation] = []
    diagnostics: list[tuple[str, str]] = []
    for registration in registrations:
        if roles.get(registration.source_id) != "migration":
            continue
        if len(primary) != 1:
            code = "SOURCE_RELATION_AMBIGUOUS" if primary else "SOURCE_RELATION_REQUIRED"
            diagnostics.append((code, registration.source_id))
            continue
        kind = (
            SourceRelationKind.LOCALIZED_MEMBER_OF
            if registration.kind is SourceKind.LOCALIZED_STRINGS
            else SourceRelationKind.TRANSLATION_FOR
        )
        relations.append(
            SourceRelation(
                stable_relation_id(kind, registration.source_id, primary[0]),
                kind,
                registration.source_id,
                primary[0],
            )
        )
    for registration in registrations:
        if (
            registration.bilingual_capability is BilingualCapability.REQUIRES_RELATION
            and not any(item.from_source_id == registration.source_id for item in relations)
            and not any(
                code == "SOURCE_RELATION_REQUIRED" and source == registration.source_id for code, source in diagnostics
            )
        ):
            diagnostics.append(("SOURCE_RELATION_REQUIRED", registration.source_id))
    return SourceRegistrySnapshot(tuple(registrations), tuple(relations), tuple(diagnostics))


def legacy_source_role(value: SourceRegistration | Mapping[str, Any]) -> str | None:
    """Return a legacy workflow role without reintroducing it into schema v3.

    Canonical registrations preserve migration-only fields inside ``legacy``.
    Old descriptors remain readable through this facade until their project is
    migrated, so presentation callers never need to inspect a top-level role.
    """

    if isinstance(value, SourceRegistration):
        legacy = dict(value.legacy)
        role = legacy.get("role")
    else:
        nested = value.get("legacy")
        if isinstance(nested, Mapping):
            role = nested.get("role")
        elif "source_id" not in value or "bilingual_capability" not in value:
            role = value.get("role")
        else:
            role = None
    normalized = "" if role is None else str(role).strip().lower()
    return normalized or None


def plugin_source_location(value: SourceRegistration | Mapping[str, Any]) -> str | None:
    """Return an enabled plugin location across legacy, V2, and current source shapes."""

    if isinstance(value, SourceRegistration):
        if not value.enabled or value.format_id is not FormatId.PLUGIN_SSE:
            return None
        return value.location

    if value.get("enabled", True) is False:
        return None
    legacy = value.get("legacy")
    legacy_values = legacy if isinstance(legacy, Mapping) else {}
    format_id = str(value.get("format_id") or "").strip().lower()
    kind = str(value.get("kind") or "").strip().lower()
    legacy_type = str(value.get("type") or legacy_values.get("type") or "").strip().lower()
    if (
        format_id != FormatId.PLUGIN_SSE.value
        and kind != SourceKind.PLUGIN.value
        and legacy_type not in {"esp", "esm", "esl", "plugin"}
    ):
        return None
    location = value.get("location") or value.get("path") or legacy_values.get("path")
    normalized = "" if location is None else str(location).strip()
    return normalized or None


def select_workbench_source(
    values: Iterable[Mapping[str, Any]],
    *,
    active_source_id: str | None = None,
) -> Mapping[str, Any]:
    """Choose a source for the legacy single-content Workbench projection."""

    sources = tuple(values)
    if active_source_id:
        for source in sources:
            identities = (source.get("source_id"), source.get("namespace"), source.get("location"))
            if active_source_id in {str(item) for item in identities if item is not None}:
                return source
    return next(
        (source for source in sources if legacy_source_role(source) == "primary"), sources[0] if sources else {}
    )


def stable_source_id(project_id: str, location: str, format_id: FormatId, *, index: int = 0) -> str:
    payload = {"project_id": project_id, "location": normalize_source_location(location), "format_id": format_id.value}
    # Index is only a deterministic collision disambiguator for malformed legacy arrays.
    if index:
        payload["legacy_index"] = index
    return f"src-{_digest(payload)[:24]}"


def stable_relation_id(kind: SourceRelationKind, from_source_id: str, to_source_id: str) -> str:
    return f"rel-{_digest({'kind': kind.value, 'from': from_source_id, 'to': to_source_id})[:24]}"


def normalize_source_location(value: str) -> str:
    location = _required_text(value, "location")
    if "\x00" in location:
        raise ValueError("source location contains a NUL character")
    if "://" in location:
        return location
    return os.path.normpath(os.path.abspath(location))


def _source_shape(format_id: FormatId) -> tuple[SourceKind, BilingualCapability]:
    if format_id is FormatId.PLUGIN_SSE:
        return SourceKind.PLUGIN, BilingualCapability.NONE
    if format_id in {FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS}:
        return SourceKind.LOCALIZED_STRINGS, BilingualCapability.REQUIRES_RELATION
    if format_id in {FormatId.XML_EET, FormatId.XML_XT, FormatId.JSON_PARATRANZ, FormatId.JSON_TRANSBRIDGE}:
        return SourceKind.BILINGUAL, BilingualCapability.SELF_CONTAINED
    return SourceKind.TRANSLATION, BilingualCapability.REQUIRES_RELATION


def _legacy_format_id(value: Mapping[str, Any], location: str) -> FormatId:
    raw = value.get("format_id")
    if raw:
        return FormatId(str(raw))
    kind = str(value.get("type") or "").lower()
    suffix = os.path.splitext(location)[1].lower()
    if kind in {"esp", "esm", "esl", "plugin"} or suffix in {".esp", ".esm", ".esl"}:
        return FormatId.PLUGIN_SSE
    if suffix in {".strings", ".dlstrings", ".ilstrings"}:
        return FormatId(f"strings{suffix}")
    if suffix == ".xml":
        return FormatId.XML_EET
    if suffix == ".json":
        return FormatId.JSON_TRANSBRIDGE
    raise ValueError("legacy Project source format cannot be inferred")


def _reject_cycles(relations: tuple[SourceRelation, ...]) -> None:
    graph: dict[str, set[str]] = {}
    for relation in relations:
        graph.setdefault(relation.from_source_id, set()).add(relation.to_source_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("Project source relation graph must be acyclic")
        if node in visited:
            return
        visiting.add(node)
        for target in graph.get(node, ()):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for source_id in graph:
        visit(source_id)


def _canonical_items(values: tuple[tuple[str, Any], ...], label: str) -> tuple[tuple[str, Any], ...]:
    if any(not isinstance(key, str) or not key.strip() for key, _ in values):
        raise ValueError(f"{label} keys must be non-empty strings")
    if len({key for key, _ in values}) != len(values):
        raise ValueError(f"{label} keys must be unique")
    ordered = tuple(sorted(values))
    json.dumps(dict(ordered), ensure_ascii=False, allow_nan=False, sort_keys=True)
    return ordered


def _valid_id(value: str) -> bool:
    return (
        isinstance(value, str) and bool(_ID.fullmatch(value)) and value not in {".", ".."} and not value.endswith(".")
    )


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "BilingualCapability",
    "SourceKind",
    "SourceRegistration",
    "SourceRelation",
    "SourceRelationKind",
    "SourceRegistrySnapshot",
    "plugin_source_location",
    "migrate_legacy_source_registry",
    "legacy_source_role",
    "normalize_source_location",
    "stable_relation_id",
    "stable_source_id",
    "select_workbench_source",
]
