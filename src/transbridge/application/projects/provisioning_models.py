"""Immutable contracts and validation for two-phase Project provisioning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol

from transbridge.application.contracts import Diagnostic, OperationResult, RequestContext
from transbridge.application.io import EntrySnapshot, FormatId, SourceSnapshot
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantRef
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.variant import SourceBaseline, VariantSnapshot

from .models import ActiveProject


@dataclass(frozen=True, slots=True)
class ProjectSourceRequest:
    """One source to validate and parse without retaining writable objects."""

    location: str
    format_hint: FormatId | None = None
    expected_fingerprint: str | None = None
    options: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        location = self.location.strip()
        if not location:
            raise ValueError("Project source location must not be empty")
        if self.expected_fingerprint is not None and not _is_sha256(self.expected_fingerprint):
            raise ValueError("expected source fingerprint must be a lowercase SHA-256 digest")
        options = _normalize_options(self.options)
        format_hint = self.format_hint
        if format_hint is not None and not isinstance(format_hint, FormatId):
            format_hint = FormatId(format_hint)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "format_hint", format_hint)
        object.__setattr__(self, "options", options)

    def fingerprint_data(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "format_hint": None if self.format_hint is None else self.format_hint.value,
            "expected_fingerprint": self.expected_fingerprint,
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class ProjectProvisioningRequest:
    project_name: str
    default_variant_name: str = "默认"
    source: ProjectSourceRequest | None = None
    migration_sources: tuple[ProjectSourceRequest, ...] = ()
    parse_options: tuple[tuple[str, Any], ...] = ()
    additional_sources: tuple[ProjectSourceRequest, ...] = ()

    def __post_init__(self) -> None:
        project_name = _display_name(self.project_name, "Project name")
        variant_name = _display_name(self.default_variant_name, "Variant name")
        if self.source is not None and not isinstance(self.source, ProjectSourceRequest):
            raise TypeError("source must be a ProjectSourceRequest or None")
        if any(not isinstance(item, ProjectSourceRequest) for item in self.migration_sources):
            raise TypeError("migration_sources must contain ProjectSourceRequest values")
        if any(not isinstance(item, ProjectSourceRequest) for item in self.additional_sources):
            raise TypeError("additional_sources must contain ProjectSourceRequest values")
        migration_fingerprints = tuple(
            hashlib.sha256(_canonical_json(item.fingerprint_data())).hexdigest() for item in self.migration_sources
        )
        if len(migration_fingerprints) != len(set(migration_fingerprints)):
            raise ValueError("migration sources must not contain duplicates")
        parse_options = _normalize_options(self.parse_options)
        object.__setattr__(self, "project_name", project_name)
        object.__setattr__(self, "default_variant_name", variant_name)
        object.__setattr__(self, "migration_sources", tuple(self.migration_sources))
        object.__setattr__(self, "parse_options", parse_options)
        object.__setattr__(self, "additional_sources", tuple(self.additional_sources))

    @property
    def sources(self) -> tuple[ProjectSourceRequest, ...]:
        """Original sources, preserving the legacy single-source position."""

        return (() if self.source is None else (self.source,)) + self.additional_sources

    @property
    def request_fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.fingerprint_data())).hexdigest()

    def fingerprint_data(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "default_variant_name": self.default_variant_name,
            "source": None if self.source is None else self.source.fingerprint_data(),
            "additional_sources": [item.fingerprint_data() for item in self.additional_sources],
            "migration_sources": [item.fingerprint_data() for item in self.migration_sources],
            "parse_options": dict(self.parse_options),
        }


@dataclass(frozen=True, slots=True)
class PreparedProjectSource:
    """Read-only source candidate returned by a parser/migration adapter."""

    descriptor: tuple[tuple[str, Any], ...]
    baseline: SourceBaseline
    diagnostics: tuple[Diagnostic, ...] = ()
    hydration: PreparedSourceHydration | None = None

    def __post_init__(self) -> None:
        descriptor = dict(self.descriptor)
        _canonical_json(descriptor)
        if len(descriptor) != len(self.descriptor):
            raise ValueError("prepared source descriptor contains duplicate keys")
        if not str(descriptor.get("location", "")).strip():
            raise ValueError("prepared source descriptor requires a normalized location")
        if descriptor.get("fingerprint") != self.baseline.fingerprint.sha256:
            raise ValueError("prepared source descriptor and baseline fingerprint differ")
        object.__setattr__(self, "descriptor", tuple(sorted(self.descriptor)))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.descriptor)


@dataclass(frozen=True, slots=True)
class PreparedSourceHydration:
    """Ephemeral source read model retained across prepare/commit."""

    location: str
    fingerprint: str
    format_id: FormatId
    source_snapshot: SourceSnapshot
    entries: tuple[EntrySnapshot, ...]

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise ValueError("hydration location must not be empty")
        if not _is_sha256(self.fingerprint):
            raise ValueError("hydration fingerprint must be a lowercase SHA-256 digest")
        if self.source_snapshot.sha256 != self.fingerprint:
            raise ValueError("hydration snapshot fingerprint mismatch")
        if any(not isinstance(entry, EntrySnapshot) for entry in self.entries):
            raise TypeError("hydration entries must be immutable EntrySnapshot values")


@dataclass(frozen=True, slots=True)
class ProjectProvisioningHydration:
    project_id: str
    owner_id: str
    request_fingerprint: str
    source: PreparedSourceHydration
    additional_sources: tuple[PreparedSourceHydration, ...] = ()

    @property
    def sources(self) -> tuple[PreparedSourceHydration, ...]:
        return (self.source, *self.additional_sources)


@dataclass(frozen=True, slots=True)
class ProjectProvisioningHydrationResult:
    """In-process result for a deliberately non-serializable UI read model."""

    value: ProjectProvisioningHydration | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if (self.value is None) == (not self.diagnostics):
            raise ValueError("hydration result must contain either a value or diagnostics")

    @property
    def is_success(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class ProjectProvisioningCommit:
    project: ProjectDto
    variant: VariantSnapshot
    baselines: tuple[SourceBaseline, ...]
    project_name_key: str

    def __post_init__(self) -> None:
        if self.variant.ref.project_id.value != self.project.envelope.identity:
            raise ValueError("provisioned Variant must belong to its Project")
        if self.variant.ref.identity.value not in self.project.envelope.data["variant_ids"]:
            raise ValueError("provisioned Variant must be declared by its Project")
        if self.project.envelope.data.get("active_variant_id") != self.variant.ref.identity.value:
            raise ValueError("provisioned Variant must be the Project active Variant")
        expected = tuple(item.fingerprint for item in self.baselines)
        if self.variant.source_fingerprints != tuple(sorted(expected, key=lambda item: item.namespace)):
            raise ValueError("provisioned baseline and Variant source fingerprints differ")
        if self.project_name_key != _name_key(str(self.project.envelope.data["name"])):
            raise ValueError("provisioned Project catalog key is not canonical")

    @property
    def project_ref(self) -> ProjectRef:
        return ProjectRef(ProjectId(self.project.envelope.identity))

    @property
    def variant_ref(self) -> VariantRef:
        return self.variant.ref


@dataclass(frozen=True, slots=True)
class ProjectProvisioningPreview:
    token: str
    request_fingerprint: str
    project_id: str
    variant_id: str
    project_name: str
    variant_name: str
    source_count: int
    entry_count: int
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "request_fingerprint": self.request_fingerprint,
            "project_id": self.project_id,
            "variant_id": self.variant_id,
            "project_name": self.project_name,
            "variant_name": self.variant_name,
            "source_count": self.source_count,
            "entry_count": self.entry_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class ProjectSourcePreparationPort(Protocol):
    def prepare_source(
        self,
        request: ProjectSourceRequest,
        context: RequestContext,
        *,
        role: str,
        common_options: tuple[tuple[str, Any], ...],
    ) -> PreparedProjectSource: ...


class ProjectProvisioningIdentityPort(Protocol):
    def project_exists(self, ref: ProjectRef) -> bool: ...

    def variant_exists(self, ref: VariantRef) -> bool: ...

    def project_name_exists(self, name_key: str) -> bool: ...


class ProjectProvisioningLifecyclePort(Protocol):
    @property
    def generation(self) -> int: ...

    @property
    def active(self) -> ActiveProject | None: ...

    def commit_provisioning(
        self,
        provisioning: ProjectProvisioningCommit,
        candidate: ActiveProject,
        expected_generation: int,
        expected_active_signature: tuple[str, int, str | None, int | None] | None,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]: ...


def _display_name(value: str, label: str) -> str:
    name = value.strip()
    if not name or len(name) > 80 or any(character in name for character in "\r\n\t"):
        raise ValueError(f"{label} must be 1-80 printable characters")
    return name


def _name_key(value: str) -> str:
    return value.strip().casefold()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("provisioning options must contain finite JSON values") from exc


def _normalize_options(values: tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    if any(not isinstance(key, str) or not key.strip() for key, _ in values):
        raise ValueError("provisioning option keys must be non-empty strings")
    if len({key for key, _ in values}) != len(values):
        raise ValueError("provisioning option keys must be unique")
    normalized = tuple(sorted(values, key=lambda item: item[0]))
    _canonical_json(dict(normalized))
    return normalized


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
