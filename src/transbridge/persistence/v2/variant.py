"""Complete Variant snapshots and replace materialization.

The aggregate in this module is the persistence authority.  Application entry
collections remain projections: their mutation contract intentionally cannot
restore persisted labels, revisions, or provenance verbatim.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, RequestContext
from transbridge.application.io.identity import EntryKey, EntryRevision, Provenance, SourceNamespace
from transbridge.application.io.stage_policy import Stage

from .ids import VariantRef
from .models import SchemaEnvelope, VariantDto

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
        inferred = tuple(sorted(set(self.inferred_fields)))
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "labels", labels)
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "inferred_fields", inferred)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key.to_dict(),
            "translation": self.translation,
            "stage": self.stage.value,
            "labels": list(self.labels),
            "provenance": [item.to_dict() for item in self.provenance],
            "revision": self.revision.value,
            "tombstone": self.tombstone,
            "inferred_fields": list(self.inferred_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VariantEntryState:
        entry_key = data.get("entry_key")
        if not isinstance(entry_key, dict):
            raise ValueError("variant entry_key must be an object")
        return cls(
            entry_key=EntryKey.from_dict(entry_key),
            translation=str(data.get("translation", "")),
            stage=int(data.get("stage", Stage.UNTRANSLATED.value)),
            labels=tuple(str(value) for value in data.get("labels", ())),
            provenance=tuple(Provenance.from_dict(value) for value in data.get("provenance", ())),
            revision=EntryRevision(data.get("revision", 0)),
            tombstone=bool(data.get("tombstone", False)),
            inferred_fields=tuple(str(value) for value in data.get("inferred_fields", ())),
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
    entries: tuple[VariantEntryState, ...]
    revision: int = 0
    label_library: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("variant snapshot revision must be a non-negative integer")
        namespaces = [item.namespace for item in self.source_fingerprints]
        if len(set(namespaces)) != len(namespaces):
            raise ValueError("variant snapshot contains duplicate source namespaces")
        keys = [entry.entry_key for entry in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("variant snapshot contains duplicate EntryKeys")
        if any(key.namespace not in set(namespaces) for key in keys):
            raise ValueError("variant entry namespace is not declared by the snapshot")
        frozen_library = tuple(sorted((str(key), _freeze_json(value)) for key, value in self.label_library))
        object.__setattr__(
            self, "source_fingerprints", tuple(sorted(self.source_fingerprints, key=lambda x: x.namespace))
        )
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=lambda x: x.entry_key)))
        object.__setattr__(self, "label_library", frozen_library)

    def to_dto(self) -> VariantDto:
        translations: dict[str, str] = {}
        labels: dict[str, list[str]] = {}
        for entry in self.entries:
            compatibility_key = _compatibility_key(entry.entry_key)
            translations[compatibility_key] = entry.translation
            labels[compatibility_key] = list(entry.labels)
        data = {
            "project_id": self.ref.project_id.value,
            "translations": translations,
            "labels": labels,
            "label_library": {key: _thaw_json(value) for key, value in self.label_library},
            "source_fingerprints": [item.to_dict() for item in self.source_fingerprints],
            "entries": [entry.to_dict() for entry in self.entries],
            "snapshot_revision": self.revision,
        }
        return VariantDto(SchemaEnvelope(2, self.ref.kind, self.ref.identity.value, self.revision, data))

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
    entries: tuple[VariantEntryState, ...]
    label_library: tuple[tuple[str, Any], ...]
    run_id: str


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


@dataclass(frozen=True, slots=True)
class LegacyProjection:
    entry: Any
    translation: str
    stage: int


def plan_legacy_variant_projection(
    entries: Iterable[Any],
    translations: Mapping[str, str],
    stages: Mapping[str, int],
    *,
    source_baseline: Iterable[Any] | None,
) -> tuple[tuple[LegacyProjection, ...], bool]:
    """Plan the lossy old-list projection without mutating caller state.

    Missing values can only be replaced when the caller supplies the source
    baseline.  Returning ``False`` as the second item exposes that migration
    boundary instead of silently treating process state as a baseline.
    """

    baseline = None
    if source_baseline is not None:
        baseline = {entry.id: (entry.translation, entry.stage) for entry in source_baseline if entry.id}
    projections: list[LegacyProjection] = []
    complete = True
    for entry in entries:
        if not entry.id:
            continue
        if entry.id in translations:
            translation = translations[entry.id]
            stage = stages.get(entry.id, entry.stage)
        elif baseline is not None and entry.id in baseline:
            translation, stage = baseline[entry.id]
        else:
            complete = False
            continue
        projections.append(LegacyProjection(entry, translation, stage))
    return tuple(projections), complete


class VariantAggregate:
    """In-memory authority with one validation/commit boundary."""

    def __init__(self, snapshot: VariantSnapshot) -> None:
        self.ref = snapshot.ref
        self._revision = snapshot.revision
        self._fingerprints = snapshot.source_fingerprints
        self._entries = {entry.entry_key: entry for entry in snapshot.entries}
        self._label_library = snapshot.label_library

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> VariantSnapshot:
        return VariantSnapshot(
            self.ref,
            self._fingerprints,
            tuple(self._entries.values()),
            self._revision,
            self._label_library,
        )

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

        projected = VariantSnapshot(
            self.ref,
            change_set.source_fingerprints,
            change_set.entries,
            self._revision + 1,
            change_set.label_library,
        )
        if before_commit is not None:
            before_commit(change_set)
        self._fingerprints = projected.source_fingerprints
        self._entries = {entry.entry_key: entry for entry in projected.entries}
        self._label_library = projected.label_library
        self._revision = projected.revision
        return self._revision


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
    return key.local_key if key.namespace == SourceNamespace.legacy() else key.serialize()


def _freeze_json(value: Any) -> Any:
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
    "LegacyProjection",
    "MaterializationResult",
    "SourceBaseline",
    "SourceFingerprint",
    "VariantAggregate",
    "VariantChangeSet",
    "VariantEntryState",
    "VariantMaterializer",
    "VariantSnapshot",
    "collect_variant_snapshot",
    "plan_legacy_variant_projection",
]
