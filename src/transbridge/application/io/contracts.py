"""Pure application contracts for translation format I/O."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    OperationOutcome,
    RequestContext,
)

from .identity import SourceNamespace

if TYPE_CHECKING:
    from .stage_policy import StagePolicyPort

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FormatId(StrEnum):
    PLUGIN_SSE = "plugin.sse"
    XML_EET = "xml.eet"
    BINARY_EET = "binary.eet"
    XML_XT = "xml.xt"
    JSON_PARATRANZ = "json.paratranz"
    JSON_DSD = "json.dsd"
    JSON_TRANSBRIDGE = "json.transbridge"
    SST_SSU8 = "sst.ssu8"
    SST_SSU9 = "sst.ssu9"
    STRINGS = "strings.strings"
    DLSTRINGS = "strings.dlstrings"
    ILSTRINGS = "strings.ilstrings"


class CapabilityLevel(StrEnum):
    UNAVAILABLE = "unavailable"
    EXPERIMENTAL = "experimental"
    SUPPORTED = "supported"


_CAPABILITY_RANK = {
    CapabilityLevel.UNAVAILABLE: 0,
    CapabilityLevel.EXPERIMENTAL: 1,
    CapabilityLevel.SUPPORTED: 2,
}


@dataclass(frozen=True, slots=True)
class FormatCapability:
    read: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    write: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    round_trip: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    localized: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    streaming: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    cancel: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    fidelity: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    gui: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    agent: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    mcp: CapabilityLevel = CapabilityLevel.UNAVAILABLE
    publish: CapabilityLevel = CapabilityLevel.UNAVAILABLE

    @classmethod
    def unavailable(cls) -> FormatCapability:
        return cls()

    def bounded_by(self, ceiling: FormatCapability) -> FormatCapability:
        """Clamp every claim to the architecture-approved maximum level."""

        values = {
            name: min(
                (getattr(self, name), getattr(ceiling, name)),
                key=lambda value: _CAPABILITY_RANK[value],
            )
            for name in self.__dataclass_fields__
        }
        return FormatCapability(**values)

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name).value for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FormatCapabilitySnapshot:
    format_id: FormatId
    capability: FormatCapability
    policy_ceiling: FormatCapability
    adapter_id: str | None = None
    adapter_version: str | None = None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id.value,
            "capability": self.capability.to_dict(),
            "policy_ceiling": self.policy_ceiling.to_dict(),
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    uri: str
    display_name: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not self.uri or not self.uri.strip():
            raise ValueError("source uri must not be empty")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("source size must not be negative")

    @property
    def suffix(self) -> str:
        return Path(self.display_name or self.uri).suffix.lower()


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source: SourceDescriptor
    format_id: FormatId
    sha256: str
    size_bytes: int
    content: bytes | None = None
    lease_id: str | None = None
    encoding: str | None = None
    bom: bytes = b""
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("source snapshot requires a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("source snapshot size must not be negative")
        if self.content is None and not self.lease_id:
            raise ValueError("path-backed source snapshots require an explicit lease_id")
        if self.content is not None:
            if len(self.content) != self.size_bytes:
                raise ValueError("source snapshot size does not match content")
            if hashlib.sha256(self.content).hexdigest() != self.sha256:
                raise ValueError("source snapshot hash does not match content")
            if self.bom and not self.content.startswith(self.bom):
                raise ValueError("source snapshot BOM does not match content")

    @classmethod
    def from_bytes(
        cls,
        source: SourceDescriptor,
        format_id: FormatId,
        content: bytes,
        *,
        encoding: str | None = None,
        bom: bytes = b"",
        metadata: tuple[tuple[str, Any], ...] = (),
    ) -> SourceSnapshot:
        return cls(
            source=source,
            format_id=format_id,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            content=content,
            encoding=encoding,
            bom=bom,
            metadata=metadata,
        )


class CancellationToken(Protocol):
    @property
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ParseRequest:
    source: SourceDescriptor
    context: RequestContext
    format_hint: FormatId | None = None
    source_namespace: SourceNamespace | None = None
    options: tuple[tuple[str, Any], ...] = ()
    cancellation: CancellationToken | None = None


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    source: SourceDescriptor
    content: bytes
    format_hint: FormatId | None = None


class ProbeStatus(StrEnum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class ProbeEvidenceKind(StrEnum):
    MAGIC = "magic"
    SCHEMA = "schema"
    ROOT_ELEMENT = "root_element"
    STRUCTURE = "structure"
    EXTENSION = "extension"
    EXPLICIT_HINT = "explicit_hint"


class ProbeConfidence(StrEnum):
    EXACT = "exact"
    HINT = "hint"


@dataclass(frozen=True, slots=True)
class ProbeEvidence:
    format_id: FormatId
    kind: ProbeEvidenceKind
    value: str
    confidence: ProbeConfidence


@dataclass(frozen=True, slots=True)
class FormatProbe:
    status: ProbeStatus
    candidates: tuple[FormatId, ...] = ()
    evidence: tuple[ProbeEvidence, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        unique = tuple(dict.fromkeys(self.candidates))
        if unique != self.candidates:
            raise ValueError("format probe candidates must be unique")
        if self.status is ProbeStatus.EXACT and len(self.candidates) != 1:
            raise ValueError("exact probes require one candidate")
        if self.status is ProbeStatus.AMBIGUOUS and len(self.candidates) < 2:
            raise ValueError("ambiguous probes require at least two candidates")
        if self.status is ProbeStatus.UNSUPPORTED and self.candidates:
            raise ValueError("unsupported probes cannot contain candidates")


@dataclass(frozen=True, slots=True)
class ParseStats:
    parsed: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0

    def __post_init__(self) -> None:
        if min(self.parsed, self.failed, self.skipped, self.cancelled) < 0:
            raise ValueError("parse stats must not be negative")

    @property
    def total(self) -> int:
        return self.parsed + self.failed + self.skipped + self.cancelled


@dataclass(frozen=True, slots=True)
class ParseResult:
    outcome: OperationOutcome
    format_id: FormatId
    source: SourceDescriptor
    source_snapshot: SourceSnapshot | None = None
    entries: tuple[Any, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    stats: ParseStats = ParseStats()
    adapter_id: str | None = None
    adapter_version: str | None = None
    capability: FormatCapability | None = None

    def __post_init__(self) -> None:
        errors = sum(item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics)
        if self.outcome is OperationOutcome.COMPLETED:
            if self.source_snapshot is None or self.stats.failed or self.stats.cancelled or errors:
                raise ValueError("completed parse results require a valid snapshot and no failures")
            if self.stats.parsed != len(self.entries):
                raise ValueError("completed parse stats must match entries")
        elif self.outcome is OperationOutcome.PARTIAL:
            if self.source_snapshot is None or not self.entries or self.stats.failed < 1 or errors < 1:
                raise ValueError("partial parse results require entries, a snapshot, and failure diagnostics")
            if self.stats.parsed != len(self.entries):
                raise ValueError("partial parse stats must match entries")
        elif self.outcome is OperationOutcome.FAILED:
            if self.source_snapshot is not None or self.entries or self.stats.failed < 1 or errors < 1:
                raise ValueError("failed parse results cannot carry entries or a source snapshot")
        elif self.outcome is OperationOutcome.CANCELLED:
            if self.source_snapshot is not None or self.entries or self.stats.cancelled < 1:
                raise ValueError("cancelled parse results cannot carry publishable state")

    @classmethod
    def completed(
        cls,
        format_id: FormatId,
        source: SourceDescriptor,
        source_snapshot: SourceSnapshot,
        entries: tuple[Any, ...] = (),
        **metadata: Any,
    ) -> ParseResult:
        return cls(
            OperationOutcome.COMPLETED,
            format_id,
            source,
            source_snapshot,
            entries,
            stats=ParseStats(parsed=len(entries)),
            **metadata,
        )


@dataclass(frozen=True, slots=True)
class WriteRequest:
    target: SourceDescriptor
    format_id: FormatId
    entries: tuple[Any, ...]
    variant_revision: int
    context: RequestContext
    source_snapshot: SourceSnapshot | None = None
    new_template: bytes | None = None
    options: tuple[tuple[str, Any], ...] = ()
    cancellation: CancellationToken | None = None
    stage_policy: StagePolicyPort | None = None

    def __post_init__(self) -> None:
        if self.variant_revision < 0:
            raise ValueError("variant revision must not be negative")
        if (self.source_snapshot is None) == (self.new_template is None):
            raise ValueError("write request requires exactly one source snapshot or new template")
