"""Complete Variant snapshots and replace materialization.

The aggregate in this module is the persistence authority.  Application entry
collections remain projections: their mutation contract intentionally cannot
restore persisted labels, revisions, or provenance verbatim.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
import json
import re
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, RequestContext
from transbridge.application.io.identity import (
    EntryKey,
    EntryRevision,
    ExternalEntryRef,
    Provenance,
    SourceNamespace,
)
from transbridge.application.io.stage_policy import Stage

from .deferred_variant_rows import DeferredVariantRows
from .ids import VariantRef
from .models import SCHEMA_VERSION, SchemaEnvelope, VariantDto

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    namespace: SourceNamespace
    sha256: str | None

    def __post_init__(self) -> None:
        if self.sha256 is not None and not _SHA256.fullmatch(self.sha256):
            raise ValueError("source fingerprint must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, str | None]:
        return {"namespace": self.namespace.value, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceFingerprint:
        value = data.get("sha256")
        return cls(SourceNamespace(str(data["namespace"])), None if value is None else str(value))


@dataclass(frozen=True, slots=True)
class VariantEntryState:
    entry_key: EntryKey
    translation: str = ""
    stage: Stage = Stage.UNTRANSLATED
    labels: tuple[str, ...] = ()
    provenance: tuple[Provenance, ...] = ()
    revision: EntryRevision = EntryRevision()
    tombstone: bool = False
    inferred_fields: tuple[str, ...] = ()
    external_refs: tuple[ExternalEntryRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.translation, str):
            raise TypeError("variant translation must be a string")
        stage = Stage.from_value(self.stage)
        if stage is None:
            raise ValueError("variant stage must be one of the supported wire values")
        labels = tuple(sorted(self.labels))
        if len(set(labels)) != len(labels) or any(not label for label in labels):
            raise ValueError("variant labels must be unique non-empty strings")
        revision = self.revision if isinstance(self.revision, EntryRevision) else EntryRevision(self.revision)
        external_refs = tuple(self.external_refs)
        if len({item.index_key for item in external_refs}) != len(external_refs):
            raise ValueError("variant external_refs must not contain duplicate identities")
        inferred = tuple(sorted(set(self.inferred_fields)))
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "external_refs", external_refs)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "inferred_fields", inferred)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key.to_dict(),
            "translation": self.translation,
            "stage": self.stage.value,
            "labels": list(self.labels),
            "provenance": [item.to_dict() for item in self.provenance],
            "external_refs": [item.to_dict() for item in self.external_refs],
            "revision": self.revision.value,
            "tombstone": self.tombstone,
            "inferred_fields": list(self.inferred_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VariantEntryState:
        entry_key = data.get("entry_key")
        if not isinstance(entry_key, dict):
            raise ValueError("variant entry_key must be an object")
        inferred_fields = tuple(str(value) for value in data.get("inferred_fields", ()))
        if "external_refs" not in data:
            inferred_fields = (*inferred_fields, "external_refs")
        return cls(
            entry_key=EntryKey.from_dict(entry_key),
            translation=str(data.get("translation", "")),
            stage=int(data.get("stage", Stage.UNTRANSLATED.value)),
            labels=tuple(str(value) for value in data.get("labels", ())),
            provenance=tuple(Provenance.from_dict(value) for value in data.get("provenance", ())),
            external_refs=tuple(ExternalEntryRef.from_dict(value) for value in data.get("external_refs", ())),
            revision=EntryRevision(data.get("revision", 0)),
            tombstone=bool(data.get("tombstone", False)),
            inferred_fields=inferred_fields,
        )


@dataclass(frozen=True, slots=True)
class SourceBaseline:
    fingerprint: SourceFingerprint
    entries: tuple[VariantEntryState, ...]

    def __post_init__(self) -> None:
        if self.fingerprint.sha256 is None:
            raise ValueError("a materialized source baseline requires a verified fingerprint")
        keys = [entry.entry_key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("source baseline contains duplicate EntryKeys")
        if any(key.namespace != self.fingerprint.namespace for key in keys):
            raise ValueError("source baseline entries must belong to its namespace")


@dataclass(frozen=True, slots=True)
class VariantSnapshot:
    ref: VariantRef
    source_fingerprints: tuple[SourceFingerprint, ...]
    entries: tuple[VariantEntryState, ...] | DeferredVariantRows
    revision: int = 0
    label_library: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("variant snapshot revision must be a non-negative integer")
        namespaces = [item.namespace for item in self.source_fingerprints]
        namespace_set = set(namespaces)
        if len(namespace_set) != len(namespaces):
            raise ValueError("variant snapshot contains duplicate source namespaces")
        if isinstance(self.entries, tuple):
            keys = [entry.entry_key for entry in self.entries]
            if len(set(keys)) != len(keys):
                raise ValueError("variant snapshot contains duplicate EntryKeys")
            if any(key.namespace not in namespace_set for key in keys):
                raise ValueError("variant entry namespace is not declared by the snapshot")
            object.__setattr__(self, "entries", tuple(sorted(self.entries, key=lambda x: x.entry_key)))
        frozen_library = tuple(sorted((str(key), _freeze_json(value)) for key, value in self.label_library))
        object.__setattr__(
            self, "source_fingerprints", tuple(sorted(self.source_fingerprints, key=lambda x: x.namespace))
        )
        object.__setattr__(self, "label_library", frozen_library)

    def to_dto(self) -> VariantDto:
        translations: dict[str, str] = {}
        labels: dict[str, list[str]] = {}
        states = (
            self.entries if isinstance(self.entries, tuple) else tuple(sorted(self.entries, key=lambda e: e.entry_key))
        )
        for entry in states:
            compatibility_key = _compatibility_key(entry.entry_key)
            translations[compatibility_key] = entry.translation
            labels[compatibility_key] = list(entry.labels)
        data = {
            "project_id": self.ref.project_id.value,
            "translations": translations,
            "labels": labels,
            "label_library": self.label_library_data(),
            "source_fingerprints": [item.to_dict() for item in self.source_fingerprints],
            "entries": [entry.to_dict() for entry in states],
            "snapshot_revision": self.revision,
        }
        return VariantDto(SchemaEnvelope(SCHEMA_VERSION, self.ref.kind, self.ref.identity.value, self.revision, data))

    def label_library_data(self) -> dict[str, Any]:
        """Copy label metadata without serializing the Variant's entries."""
        return {key: _thaw_json(value) for key, value in self.label_library}

    @classmethod
    def from_dto(cls, dto: VariantDto, ref: VariantRef | None = None) -> VariantSnapshot:
        envelope = dto.envelope
        resolved_ref = ref
        if resolved_ref is None:
            from .ids import ProjectId, VariantId

            resolved_ref = VariantRef(VariantId(envelope.identity), ProjectId(str(envelope.data["project_id"])))
        if envelope.identity != resolved_ref.identity.value:
            raise ValueError("variant DTO identity does not match its reference")
        data = envelope.data
        fingerprints = tuple(SourceFingerprint.from_dict(item) for item in data.get("source_fingerprints", ()))
        entries = tuple(VariantEntryState.from_dict(item) for item in data.get("entries", ()))
        if not fingerprints and not entries and (data.get("translations") or data.get("labels")):
            fingerprints, entries = _read_legacy_compatibility_state(data)
        library = tuple((str(key), value) for key, value in (data.get("label_library") or {}).items())
        return cls(
            resolved_ref,
            fingerprints,
            entries,
            int(data.get("snapshot_revision", envelope.revision)),
            library,
        )


@dataclass(frozen=True, slots=True)
class VariantChangeSet:
    ref: VariantRef
    expected_revision: int
    source_fingerprints: tuple[SourceFingerprint, ...]
    entries: tuple[VariantEntryState, ...] | DeferredVariantRows
    label_library: tuple[tuple[str, Any], ...]
    run_id: str
    operation_id: str | None = None
    application_outcomes: tuple[tuple[EntryKey, str, str, int], ...] = ()
    sparse_entries: tuple[VariantEntryState, ...] = ()


@dataclass(frozen=True, slots=True)
class FingerprintConflict:
    namespace: SourceNamespace
    stored_sha256: str | None
    current_sha256: str | None


@dataclass(frozen=True, slots=True)
class FingerprintMigrationPlan:
    conflicts: tuple[FingerprintConflict, ...]
    action: str = "reconcile source identity and explicitly remap EntryKeys"


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    committed: bool
    revision: int
    diagnostics: tuple[Diagnostic, ...] = ()
    migration_plan: FingerprintMigrationPlan | None = None


class VariantAggregate:
    """In-memory authority with one validation/commit boundary."""

    def __init__(
        self,
        snapshot: VariantSnapshot,
        *,
        applications: Iterable[tuple[str, tuple[tuple[EntryKey, str, str, int], ...]]] = (),
    ) -> None:
        self.ref = snapshot.ref
        self._revision = snapshot.revision
        self._fingerprints = snapshot.source_fingerprints
        self._entries = (
            None
            if isinstance(snapshot.entries, DeferredVariantRows)
            else {entry.entry_key: entry for entry in snapshot.entries}
        )
        self._label_library = snapshot.label_library
        self._snapshot = snapshot
        self._base_revision = snapshot.revision
        self._changes: dict[EntryKey, int] = {}
        self._applications = dict(applications)

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> VariantSnapshot:
        return self._snapshot

    def changes_since(self, revision: int | None) -> tuple[VariantEntryState, ...] | None:
        if revision is None or revision < self._base_revision:
            return None
        return tuple(
            self._snapshot.entries.get(key) if self._entries is None else self._entries[key]
            for key, changed in self._changes.items()
            if changed > revision
        )

    def acknowledge_changes(
        self, revision: int, project_revision: int, *, saved_snapshot: VariantSnapshot, applications=()
    ) -> None:
        self._changes = {key: changed for key, changed in self._changes.items() if changed > revision}
        self._base_revision = max(self._base_revision, revision)
        for operation_id, outcomes in applications:
            if self._applications.get(operation_id) == outcomes:
                self._applications.pop(operation_id)
        if isinstance(saved_snapshot.entries, DeferredVariantRows) and isinstance(
            self._snapshot.entries, DeferredVariantRows
        ):
            later = tuple(self._snapshot.entries.get(key) for key in self._changes)
            base = saved_snapshot.entries.rebased(project_revision, revision)
            self._snapshot = replace(self._snapshot, entries=base.patched(later))

    def rebase_project_revision(self, project_revision: int, persisted_variant_revision: int) -> None:
        if isinstance(self._snapshot.entries, DeferredVariantRows):
            rows = self._snapshot.entries.rebased(project_revision, persisted_variant_revision, keep_changes=True)
            self._snapshot = replace(self._snapshot, entries=rows)

    def pending_applications(self) -> tuple[tuple[str, tuple[tuple[EntryKey, str, str, int], ...]], ...]:
        return tuple(self._applications.items())

    def rebase_committed_save(self, saved, project_revision, observed_revision, observed_entries, applications):
        """Install a proven save while retaining edits made after it was prepared."""
        later = self.changes_since(observed_revision)
        if later is None:
            raise ValueError("The working copy changed beyond the saved edit range")
        entries = saved.entries
        if isinstance(entries, DeferredVariantRows):
            entries = entries.rebased(project_revision, saved.revision)
            saved_states = {entry.entry_key: entry for entry in entries.get_many(row.entry_key for row in later)}
        else:
            saved_states = {entry.entry_key: entry for entry in entries}
        observed = {entry.entry_key: entry for entry in observed_entries}
        changes = []
        for row in later:
            persisted = saved_states.get(row.entry_key)
            baseline = observed.get(row.entry_key)
            combined = _retain_late_entry_fields(persisted, baseline, row) if persisted and baseline else row
            if combined != persisted:
                if persisted is not None and combined.revision <= persisted.revision:
                    combined = replace(combined, revision=persisted.revision.next())
                changes.append(combined)
        if isinstance(entries, DeferredVariantRows):
            entries = entries.patched(changes)
        else:
            saved_states.update((row.entry_key, row) for row in changes)
            entries = tuple(saved_states.values())
        dirty = bool(changes) or (
            self._fingerprints != saved.source_fingerprints or self._label_library != saved.label_library
        )
        revision = max(self.revision, saved.revision + 1) if dirty else saved.revision
        remaining = self._applications.copy()
        for operation_id, outcomes in applications:
            if remaining.get(operation_id) == outcomes:
                remaining.pop(operation_id)
        result = VariantAggregate(
            VariantSnapshot(saved.ref, self._fingerprints, entries, revision, self._label_library),
            applications=remaining.items(),
        )
        result._base_revision = saved.revision
        result._changes = {row.entry_key: revision for row in changes}
        return result

    def fork(self) -> VariantAggregate:
        copy = VariantAggregate(self._snapshot)
        copy._base_revision = self._base_revision
        copy._changes = dict(self._changes)
        copy._applications = dict(self._applications)
        return copy

    def commit(
        self,
        change_set: VariantChangeSet,
        context: RequestContext,
        *,
        before_commit: Callable[[VariantChangeSet], None] | None = None,
    ) -> int:
        if change_set.ref != self.ref:
            raise ValueError("variant ChangeSet targets a different aggregate")
        if change_set.expected_revision != self._revision:
            raise ValueError("variant aggregate revision conflict")
        if not change_set.run_id or context.run_id != change_set.run_id:
            raise ValueError("variant ChangeSet run identity mismatch")
        if context.project_id is not None and context.project_id != self.ref.project_id.value:
            raise ValueError("request context project does not own the Variant")
        if context.variant_id is not None and context.variant_id != self.ref.identity.value:
            raise ValueError("request context targets a different Variant")

        entries = change_set.entries
        if isinstance(entries, DeferredVariantRows) and isinstance(self._snapshot.entries, DeferredVariantRows):
            # An intervening save may acknowledge the same working revision and
            # advance its pinned disk token while this change is being prepared.
            current = self._snapshot.entries
            entries = current.patched(change_set.sparse_entries) if change_set.sparse_entries else current
        operation_id = change_set.operation_id
        if operation_id is not None:
            if (
                operation_id in self._applications
                and self._applications[operation_id] != change_set.application_outcomes
            ):
                raise ValueError("operation ID already identifies a different application")
            if (
                entries == self._snapshot.entries
                and change_set.source_fingerprints == self._snapshot.source_fingerprints
                and change_set.label_library == self._snapshot.label_library
            ):
                if before_commit is not None:
                    before_commit(change_set)
                self._applications[operation_id] = change_set.application_outcomes
                return self._revision

        projected = VariantSnapshot(
            self.ref,
            change_set.source_fingerprints,
            entries,
            self._revision + 1,
            change_set.label_library,
        )
        if before_commit is not None:
            before_commit(change_set)
        self._fingerprints = projected.source_fingerprints
        self._record_entries(projected, change_set.sparse_entries)
        self._label_library = projected.label_library
        self._revision = projected.revision
        self._snapshot = projected
        if operation_id is not None:
            self._applications[operation_id] = change_set.application_outcomes
        return self._revision

    def _record_entries(self, projected, sparse):
        if isinstance(projected.entries, DeferredVariantRows):
            self._changes.update((entry.entry_key, projected.revision) for entry in sparse)
            return
        entries = {entry.entry_key: entry for entry in projected.entries}
        previous = self._entries
        if previous is None:
            previous = {entry.entry_key: entry for entry in self._snapshot.entries}
        if previous.keys() - entries.keys():
            self._base_revision = projected.revision
            self._changes.clear()
        else:
            self._changes.update({
                key: projected.revision for key, entry in entries.items() if previous.get(key) != entry
            })
        self._entries = entries


def _retain_late_entry_fields(persisted, observed, current):
    changes = {
        name: getattr(current, name)
        for name in ("labels", "provenance", "tombstone", "inferred_fields", "external_refs")
        if getattr(current, name) != getattr(observed, name)
    }
    # Treat an explicit translation/stage edit as one decision, including an empty translation.
    if (current.translation, current.stage) != (observed.translation, observed.stage):
        changes.update(translation=current.translation, stage=current.stage)
    return replace(persisted, **changes)


class VariantMaterializer:
    def materialize(
        self,
        snapshot: VariantSnapshot,
        baselines: Iterable[SourceBaseline],
        aggregate: VariantAggregate,
        context: RequestContext,
        *,
        before_commit: Callable[[VariantChangeSet], None] | None = None,
    ) -> MaterializationResult:
        baseline_map = _baseline_map(baselines)
        stored = {item.namespace: item for item in snapshot.source_fingerprints}
        current = {namespace: baseline.fingerprint for namespace, baseline in baseline_map.items()}
        diagnostics: list[Diagnostic] = []
        conflicts: list[FingerprintConflict] = []

        for namespace in sorted(set(stored) | set(current)):
            old = stored.get(namespace)
            new = current.get(namespace)
            if old is None:
                diagnostics.append(
                    _warning("VARIANT_SOURCE_ADDED", "A source was added; baseline state was used.", namespace)
                )
            elif new is None:
                diagnostics.append(
                    _warning("VARIANT_SOURCE_REMOVED", "A stored source is no longer present.", namespace)
                )
            elif old.sha256 != new.sha256 or old.sha256 is None:
                conflicts.append(FingerprintConflict(namespace, old.sha256, new.sha256))

        if conflicts:
            diagnostics.append(
                Diagnostic(
                    "VARIANT_SOURCE_FINGERPRINT_CONFLICT",
                    "Source fingerprints changed; local-key overwrite was refused.",
                    details=(("count", len(conflicts)),),
                )
            )
            return MaterializationResult(
                False,
                aggregate.revision,
                tuple(diagnostics),
                FingerprintMigrationPlan(tuple(conflicts)),
            )

        baseline_entries = {
            namespace: {entry.entry_key: entry for entry in baseline.entries}
            for namespace, baseline in baseline_map.items()
        }
        desired = {entry_key: entry for entries in baseline_entries.values() for entry_key, entry in entries.items()}
        for entry in snapshot.entries:
            source_entries = baseline_entries.get(entry.entry_key.namespace)
            if source_entries is None:
                continue
            baseline_entry = source_entries.get(entry.entry_key)
            if baseline_entry is None:
                diagnostics.append(
                    _warning(
                        "VARIANT_ENTRY_REMOVED",
                        "A stored EntryKey is absent from the current source.",
                        entry.entry_key.namespace,
                        entry.entry_key,
                    )
                )
                continue
            desired[entry.entry_key] = baseline_entry if entry.tombstone else entry

        if not snapshot.entries:
            diagnostics.append(
                Diagnostic(
                    "VARIANT_EMPTY_BASELINE_RESTORED",
                    "The empty Variant restored every source baseline.",
                    DiagnosticSeverity.INFO,
                )
            )

        run_id = context.run_id or ""
        change_set = VariantChangeSet(
            snapshot.ref,
            aggregate.revision,
            tuple(current.values()),
            tuple(desired.values()),
            snapshot.label_library,
            run_id,
        )
        try:
            revision = aggregate.commit(change_set, context, before_commit=before_commit)
        except Exception as exc:
            diagnostics.append(
                Diagnostic(
                    "VARIANT_CHANGESET_FAILED",
                    "Variant materialization failed before commit.",
                    details=(("exception_type", type(exc).__name__),),
                )
            )
            return MaterializationResult(False, aggregate.revision, tuple(diagnostics))
        return MaterializationResult(True, revision, tuple(diagnostics))


def collect_variant_snapshot(
    ref: VariantRef,
    entries: Iterable[Any],
    source_fingerprints: Iterable[SourceFingerprint],
    *,
    entry_labels: Mapping[EntryKey | str, Iterable[str]] | None = None,
    label_library: Mapping[str, Any] | None = None,
    revision: int = 0,
    tombstones: Iterable[EntryKey] = (),
) -> VariantSnapshot:
    """Collect a complete snapshot without consulting an older cache."""

    labels = entry_labels or {}
    tombstone_set = set(tombstones)
    states: list[VariantEntryState] = []
    for entry in entries:
        key = entry.identity
        entry_label_values = labels.get(key, labels.get(key.local_key, ()))
        states.append(
            VariantEntryState(
                key,
                entry.translation,
                entry.stage,
                tuple(entry_label_values),
                tuple(entry.provenance),
                entry.revision,
                key in tombstone_set,
                external_refs=tuple(entry.external_refs),
            )
        )
    return VariantSnapshot(
        ref,
        tuple(source_fingerprints),
        tuple(states),
        revision,
        tuple((str(key), value) for key, value in (label_library or {}).items()),
    )


def _baseline_map(baselines: Iterable[SourceBaseline]) -> dict[SourceNamespace, SourceBaseline]:
    result: dict[SourceNamespace, SourceBaseline] = {}
    for baseline in baselines:
        namespace = baseline.fingerprint.namespace
        if namespace in result:
            raise ValueError("duplicate source baseline namespace")
        result[namespace] = baseline
    return result


def _warning(code: str, message: str, namespace: SourceNamespace, key: EntryKey | None = None) -> Diagnostic:
    details: tuple[tuple[str, Any], ...] = (("namespace", namespace.value),)
    if key is not None:
        details += (("entry_key", key.serialize()),)
    return Diagnostic(code, message, DiagnosticSeverity.WARNING, details=details)


def _compatibility_key(key: EntryKey) -> str:
    return key.local_key if key.namespace == SourceNamespace.legacy() and key.original is None else key.serialize()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, (_FrozenObject, _FrozenArray)):
        return value
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("label library values must be finite JSON values") from exc
    if isinstance(value, dict):
        return _FrozenObject(tuple(sorted((str(key), _freeze_json(item)) for key, item in value.items())))
    if isinstance(value, list):
        return _FrozenArray(tuple(_freeze_json(item) for item in value))
    return deepcopy(value)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, _FrozenObject):
        return {key: _thaw_json(item) for key, item in value.items}
    if isinstance(value, _FrozenArray):
        return [_thaw_json(item) for item in value.items]
    return deepcopy(value)


@dataclass(frozen=True, slots=True)
class _FrozenObject:
    items: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _FrozenArray:
    items: tuple[Any, ...]


def _read_legacy_compatibility_state(
    data: Mapping[str, Any],
) -> tuple[tuple[SourceFingerprint, ...], tuple[VariantEntryState, ...]]:
    namespace = SourceNamespace.legacy()
    translations = data.get("translations") or {}
    labels = data.get("labels") or {}
    entries = tuple(
        VariantEntryState(
            EntryKey(namespace, str(key)),
            str(translations.get(key, "")),
            Stage.TRANSLATED if translations.get(key, "") else Stage.UNTRANSLATED,
            tuple(str(value) for value in labels.get(key, ())),
            inferred_fields=("provenance", "revision", "stage"),
        )
        for key in sorted(set(translations) | set(labels))
    )
    return (SourceFingerprint(namespace, None),), entries


__all__ = [
    "FingerprintConflict",
    "FingerprintMigrationPlan",
    "MaterializationResult",
    "SourceBaseline",
    "SourceFingerprint",
    "VariantAggregate",
    "VariantChangeSet",
    "VariantEntryState",
    "VariantMaterializer",
    "VariantSnapshot",
    "collect_variant_snapshot",
]
