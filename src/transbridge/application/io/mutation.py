"""Optimistic, permission-aware entry mutation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from transbridge.application.contracts import Diagnostic, RequestContext

from .identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance

VALID_STAGES = frozenset({-1, 0, 1, 2, 3, 5, 9})
FIELD_PERMISSIONS = {
    "original": "entry.original.write",
    "translation": "entry.translation.write",
    "stage": "entry.stage.write",
    "context": "entry.context.write",
    "external_refs": "entry.external_refs.write",
    "metadata": "entry.metadata.write",
}


@dataclass(frozen=True, slots=True)
class EntryPatch:
    entry_key: EntryKey
    changes: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        names = tuple(name for name, _ in self.changes)
        if not names:
            raise ValueError("entry patch must contain at least one field")
        if len(names) != len(set(names)):
            raise ValueError("entry patch fields must be unique")
        unknown = set(names).difference(FIELD_PERMISSIONS)
        if unknown:
            raise ValueError(f"entry patch contains immutable or unknown fields: {sorted(unknown)}")
        for name, value in self.changes:
            _validate_field(name, value)

    @classmethod
    def create(cls, entry_key: EntryKey, **changes: Any) -> EntryPatch:
        return cls(entry_key, tuple(changes.items()))

    def as_dict(self) -> dict[str, Any]:
        return dict(self.changes)

    def missing_permissions(self, trusted_permissions: frozenset[str]) -> tuple[str, ...]:
        required = {FIELD_PERMISSIONS[name] for name, _ in self.changes}
        return tuple(sorted(required.difference(trusted_permissions)))


def _validate_field(name: str, value: Any) -> None:
    if name in {"original", "translation"} and not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if name == "context" and value is not None and not isinstance(value, str):
        raise TypeError("context must be a string or None")
    if name == "stage" and (isinstance(value, bool) or not isinstance(value, int) or value not in VALID_STAGES):
        raise ValueError(f"stage must be one of {sorted(VALID_STAGES)}")
    if name == "external_refs":
        if not isinstance(value, tuple) or not all(isinstance(item, ExternalEntryRef) for item in value):
            raise TypeError("external_refs must be a tuple of ExternalEntryRef")
        if len({item.index_key for item in value}) != len(value):
            raise ValueError("external_refs must not contain duplicate identities")
    if name == "metadata":
        if not isinstance(value, tuple) or not all(
            isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value
        ):
            raise TypeError("metadata must be a tuple of key/value pairs")


@dataclass(frozen=True, slots=True)
class ChangeSet:
    run_id: str
    patches: tuple[EntryPatch, ...]
    expected_revisions: tuple[tuple[EntryKey, EntryRevision], ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.run_id or not self.run_id.strip():
            raise ValueError("change set run_id must not be empty")
        if self.provenance.run_id != self.run_id:
            raise ValueError("change set and provenance run_id must match")
        patch_keys = tuple(patch.entry_key for patch in self.patches)
        expected_keys = tuple(key for key, _ in self.expected_revisions)
        if not patch_keys or len(patch_keys) != len(set(patch_keys)):
            raise ValueError("change set patch keys must be non-empty and unique")
        if len(expected_keys) != len(set(expected_keys)) or set(expected_keys) != set(patch_keys):
            raise ValueError("change set requires one expected revision for every patch")

    def expected_revision(self, entry_key: EntryKey) -> EntryRevision:
        return dict(self.expected_revisions)[entry_key]


@dataclass(frozen=True, slots=True)
class EntrySnapshot:
    entry_key: EntryKey
    legacy_id: str
    original: str
    translation: str
    stage: int
    context: str | None
    external_refs: tuple[ExternalEntryRef, ...]
    revision: EntryRevision
    provenance: tuple[Provenance, ...]
    metadata: tuple[tuple[str, Any], ...]


class MutationStatus(StrEnum):
    APPLIED = "applied"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class MutationResult:
    status: MutationStatus
    run_id: str
    previous_collection_revision: EntryRevision
    collection_revision: EntryRevision
    changed_keys: tuple[EntryKey, ...] = ()
    snapshots: tuple[EntrySnapshot, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.status is MutationStatus.APPLIED:
            if not self.changed_keys or self.collection_revision.value <= self.previous_collection_revision.value:
                raise ValueError("applied mutation must advance the collection revision")
            if self.diagnostics:
                raise ValueError("applied mutation cannot contain error diagnostics")
        elif self.collection_revision != self.previous_collection_revision:
            raise ValueError("rejected mutations cannot advance the collection revision")


@dataclass(frozen=True, slots=True)
class LegacyEntryMapping:
    legacy_id: str
    legacy_key: str
    entry_key: EntryKey


@dataclass(frozen=True, slots=True)
class LegacyMappingReport:
    mappings: tuple[LegacyEntryMapping, ...]
    ambiguous_local_keys: tuple[str, ...] = ()
    external_ref_conflicts: tuple[tuple[str, tuple[EntryKey, ...]], ...] = ()


class CollectionMutationPort(Protocol):
    @property
    def collection_revision(self) -> EntryRevision: ...

    def snapshot(self, entry_key: EntryKey) -> EntrySnapshot | None: ...

    def apply(self, change_set: ChangeSet, context: RequestContext) -> MutationResult: ...

    def legacy_mapping_report(self) -> LegacyMappingReport: ...
