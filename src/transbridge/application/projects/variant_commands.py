# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Pure builders for authoritative active-Variant commands.

The helpers in this module never mutate a projection or aggregate. They build
one complete candidate entry tuple so the lifecycle service can validate and
commit it while holding the active-project lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io.identity import EntryKey, ExternalEntryRef
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2.deferred_variant_rows import DeferredVariantRows
from transbridge.persistence.v2.variant import VariantEntryState


@dataclass(frozen=True, slots=True)
class EntryStatePatch:
    """All mutable Variant-owned state for one existing entry."""

    translation: str
    stage: int
    external_refs: tuple[ExternalEntryRef, ...] = ()


def update_entry_by_local_key(
    entries: tuple[VariantEntryState, ...],
    local_key: str,
    *,
    translation: str | None = None,
    stage: int | None = None,
) -> tuple[VariantEntryState, ...]:
    matches = tuple(entry for entry in entries if entry.entry_key.local_key == local_key)
    if not matches:
        raise ValueError("EntryKey is not present in the active Variant")
    if len(matches) != 1:
        raise ValueError("local key is ambiguous across active source namespaces")
    target = matches[0]
    next_translation = target.translation if translation is None else str(translation)
    next_stage = target.stage if stage is None else Stage(stage)
    if next_translation == target.translation and next_stage is target.stage:
        return entries
    return tuple(
        replace(
            entry,
            translation=next_translation,
            stage=next_stage,
            revision=entry.revision.next(),
        )
        if entry.entry_key == target.entry_key
        else entry
        for entry in entries
    )


def update_entry_by_key(
    entries: tuple[VariantEntryState, ...],
    entry_key: EntryKey,
    *,
    translation: str | None = None,
    stage: int | None = None,
) -> tuple[VariantEntryState, ...]:
    """Update one exact EntryKey so equal local keys across sources stay independent."""

    matches = tuple(entry for entry in entries if entry.entry_key == entry_key)
    if not matches:
        raise ValueError("EntryKey is not present in the active Variant")
    target = matches[0]
    next_translation = target.translation if translation is None else str(translation)
    next_stage = target.stage if stage is None else Stage(stage)
    if next_translation == target.translation and next_stage is target.stage:
        return entries
    return tuple(
        replace(target, translation=next_translation, stage=next_stage, revision=target.revision.next())
        if entry.entry_key == entry_key
        else entry
        for entry in entries
    )


def replace_labels(
    entries: tuple[VariantEntryState, ...] | DeferredVariantRows,
    entry_labels: Mapping[EntryKey | str, set[str]],
    *,
    only_listed: bool = False,
) -> tuple[VariantEntryState, ...] | DeferredVariantRows:
    if only_listed and not entry_labels:
        return entries
    projected: list[VariantEntryState] = []
    changes: list[VariantEntryState] = []
    exact_mode = only_listed or any(isinstance(key, EntryKey) for key in entry_labels)
    deferred = isinstance(entries, DeferredVariantRows)
    selected = (
        entries.get_many(entry_labels)
        if deferred and exact_mode and all(isinstance(key, EntryKey) for key in entry_labels)
        else entries
    )
    for entry in selected:
        values = entry_labels.get(entry.entry_key)
        if values is None and exact_mode:
            projected.append(entry)
            continue
        if values is None:
            values = entry_labels.get(entry.entry_key.serialize())
        if values is None:
            values = entry_labels.get(entry.entry_key.local_key, ())
        labels = tuple(sorted(values))
        if labels == entry.labels:
            projected.append(entry)
        else:
            changed = replace(entry, labels=labels, revision=entry.revision.next())
            projected.append(changed)
            changes.append(changed)
    if deferred:
        return entries.patched(changes) if changes else entries
    return tuple(projected)


def patch_entry_states(
    entries: tuple[VariantEntryState, ...] | DeferredVariantRows,
    states: Mapping[EntryKey, tuple[str, int]],
) -> tuple[VariantEntryState, ...] | DeferredVariantRows:
    if not states:
        return entries
    normalized = {key: (str(translation), Stage(stage)) for key, (translation, stage) in states.items()}
    deferred = isinstance(entries, DeferredVariantRows)
    selected = entries.get_many(normalized) if deferred else entries
    available = {entry.entry_key for entry in selected}
    if missing := set(normalized).difference(available):
        raise ValueError(f"translated entries are not present in the active Variant: {len(missing)}")

    projected: list[VariantEntryState] = []
    changes: list[VariantEntryState] = []
    for entry in selected:
        state = normalized.get(entry.entry_key)
        if state is None or state == (entry.translation, entry.stage):
            projected.append(entry)
            continue
        changed = replace(
            entry,
            translation=state[0],
            stage=state[1],
            revision=entry.revision.next(),
        )
        projected.append(changed)
        changes.append(changed)
    if deferred:
        return entries.patched(changes) if changes else entries
    return tuple(projected)


def patch_entry_records(
    entries: tuple[VariantEntryState, ...] | DeferredVariantRows,
    patches: Mapping[EntryKey, EntryStatePatch],
) -> tuple[VariantEntryState, ...] | DeferredVariantRows:
    """Patch translation, stage and external identity without touching source-owned text."""

    if not patches:
        return entries
    normalized = {
        key: EntryStatePatch(str(patch.translation), Stage(patch.stage), tuple(patch.external_refs))
        for key, patch in patches.items()
    }
    deferred = isinstance(entries, DeferredVariantRows)
    selected = entries.get_many(normalized) if deferred else entries
    available = {entry.entry_key for entry in selected}
    if missing := set(normalized).difference(available):
        raise ValueError(f"patched entries are not present in the active Variant: {len(missing)}")

    projected: list[VariantEntryState] = []
    changes: list[VariantEntryState] = []
    reference_namespaces = set()
    for entry in selected:
        patch = normalized.get(entry.entry_key)
        if patch is None:
            projected.append(entry)
            continue
        state = (patch.translation, patch.stage, patch.external_refs)
        if state == (entry.translation, entry.stage, entry.external_refs):
            projected.append(entry)
            continue
        changed = replace(
            entry,
            translation=patch.translation,
            stage=patch.stage,
            external_refs=patch.external_refs,
            revision=entry.revision.next(),
            inferred_fields=tuple(value for value in entry.inferred_fields if value != "external_refs"),
        )
        projected.append(changed)
        changes.append(changed)
        if patch.external_refs != entry.external_refs:
            reference_namespaces.add(entry.entry_key.namespace)
    result = (entries.patched(changes) if changes else entries) if deferred else tuple(projected)
    # Reference identity belongs to one source collection. Other sources may
    # legitimately share a remote entry; their state must not become a gate.
    if reference_namespaces:
        owners = {}
        reference_entries = result.for_sources(reference_namespaces) if deferred else result
        for entry in reference_entries:
            if entry.entry_key.namespace not in reference_namespaces:
                continue
            for reference in entry.external_refs:
                previous = owners.setdefault((entry.entry_key.namespace, reference.index_key), entry.entry_key)
                if previous != entry.entry_key:
                    raise DomainError(
                        ErrorCategory.CONFLICT,
                        "EXTERNAL_REF_CONFLICT",
                        f"external reference conflict: {reference.index_key}",
                    )
    return result


def normalize_label_library(label_library: Mapping[str, Mapping[str, Any]]) -> tuple[tuple[str, Any], ...]:
    return tuple((str(key), dict(value)) for key, value in label_library.items())


__all__ = [
    "EntryStatePatch",
    "normalize_label_library",
    "patch_entry_states",
    "patch_entry_records",
    "replace_labels",
    "update_entry_by_local_key",
    "update_entry_by_key",
]


def update_deferred_entry(lifecycle, active, snapshot, key, context, translation, stage):
    """Commit one field edit without materializing a deferred Variant range."""
    from transbridge.application.contracts import OperationResult
    from transbridge.persistence.v2.variant import VariantChangeSet

    try:
        current = snapshot.entries.get(key)
        if current is None:
            raise ValueError("当前版本没有对应词条。")
        (state,) = update_entry_by_key((current,), key, translation=translation, stage=stage)
        if state == current:
            return OperationResult.completed(active.summary(), run_id=context.run_id)
        change = VariantChangeSet(
            snapshot.ref,
            snapshot.revision,
            snapshot.source_fingerprints,
            snapshot.entries.patched((state,)),
            snapshot.label_library,
            context.run_id or "",
            sparse_entries=(state,),
        )
        return lifecycle.commit_active_variant(
            change, context, expected_project_revision=active.project.envelope.revision
        )
    except Exception as exc:
        return OperationResult.from_exception(exc, run_id=context.run_id)
