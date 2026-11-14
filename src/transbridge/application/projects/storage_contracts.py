# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read and write contracts for the translation-only project store."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState


@dataclass(frozen=True, slots=True)
class StoredEntry:
    snapshot: EntrySnapshot
    state: VariantEntryState

    def __post_init__(self) -> None:
        if self.snapshot.entry_key != self.state.entry_key:
            raise ValueError("source entry and Variant state must have the same identity")


@dataclass(frozen=True, slots=True)
class SourceInfo:
    namespace: SourceNamespace
    name: str
    format_id: FormatId
    fingerprint: str
    options: tuple[tuple[str, Any], ...] = ()
    format_fields: tuple[tuple[str, Any], ...] = ()
    reference_path: str = ""


@dataclass(frozen=True, slots=True)
class ExtractedSource:
    namespace: SourceNamespace
    name: str
    format_id: FormatId
    fingerprint: str
    entries: tuple[StoredEntry, ...]
    options: tuple[tuple[str, Any], ...] = ()
    format_fields: tuple[tuple[str, Any], ...] = ()
    format_records: tuple[tuple[str, str | None], ...] = ()
    reference_path: str = ""

    @property
    def info(self) -> SourceInfo:
        return SourceInfo(
            self.namespace,
            self.name,
            self.format_id,
            self.fingerprint,
            self.options,
            self.format_fields,
            self.reference_path,
        )


def validate_source_reference_path(path: str) -> None:
    if not isinstance(path, str) or "\x00" in path:
        raise ValueError("source reference must be path text")


@dataclass(frozen=True, slots=True)
class VariantInfo:
    identity: VariantId
    name: str
    revision: int


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    identity: ProjectId
    name: str
    revision: int
    active_variant: VariantId
    variants: tuple[VariantInfo, ...]


@dataclass(frozen=True, slots=True)
class ProjectReadToken:
    project_id: ProjectId
    variant_id: VariantId
    project_revision: int
    variant_revision: int


@dataclass(frozen=True, slots=True)
class EntryStateChange:
    key: EntryKey
    values: tuple[tuple[str, Any], ...]
    expected_revision: EntryRevision | None = None

    def __post_init__(self) -> None:
        names = {name for name, _ in self.values}
        if not self.values or len(names) != len(self.values) or not names <= EDITABLE_FIELDS:
            raise ValueError("entry change must specify distinct editable fields")


EDITABLE_FIELDS = frozenset({"translation", "stage", "labels", "provenance", "tombstone", "external_refs"})
type ApplicationOutcome = tuple[EntryKey, str, str, int]


@dataclass(frozen=True, slots=True)
class SaveBatch:
    project_id: ProjectId
    variant_id: VariantId
    expected_project_revision: int
    expected_variant_revision: int
    changes: tuple[EntryStateChange, ...]
    operation_id: str | None = None
    capture_token: str | None = None

    def __post_init__(self) -> None:
        if len({change.key for change in self.changes}) != len(self.changes):
            raise ValueError("save batch must contain distinct entry identities")


@dataclass(frozen=True, slots=True)
class SaveReceipt:
    project_id: ProjectId
    variant_id: VariantId
    project_revision: int
    variant_revision: int
    entries: tuple[VariantEntryState, ...] = ()
    operation_id: str | None = None
    capture_token: str | None = None
    application_outcomes: tuple[ApplicationOutcome, ...] = ()


class ProjectReadPort(Protocol):
    def summary(self) -> ProjectSummary: ...

    def sources(self) -> tuple[SourceInfo, ...]: ...

    def read_page(
        self, variant_id: VariantId, *, namespace: SourceNamespace | None = None, offset: int = 0, limit: int = 100
    ) -> tuple[StoredEntry, ...]: ...

    def read_entries(self, variant_id: VariantId, keys: Iterable[EntryKey]) -> tuple[StoredEntry, ...]: ...


class ProjectStorePort(ProjectReadPort, Protocol):
    def commit(self, batch: SaveBatch) -> SaveReceipt: ...
