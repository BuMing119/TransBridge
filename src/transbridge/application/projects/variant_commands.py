"""Pure builders for authoritative active-Variant commands.

The helpers in this module never mutate a projection or aggregate. They build
one complete candidate entry tuple so the lifecycle service can validate and
commit it while holding the active-project lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from transbridge.application.io.identity import EntryKey, ExternalEntryRef
from transbridge.application.io.stage_policy import Stage
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
    entries: tuple[VariantEntryState, ...],
    entry_labels: Mapping[EntryKey | str, set[str]],
) -> tuple[VariantEntryState, ...]:
    projected: list[VariantEntryState] = []
    exact_mode = any(isinstance(key, EntryKey) for key in entry_labels)
    for entry in entries:
        values = entry_labels.get(entry.entry_key)
        if values is None and exact_mode:
            projected.append(entry)
            continue
        if values is None:
            values = entry_labels.get(entry.entry_key.serialize())
        if values is None:
            values = entry_labels.get(entry.entry_key.local_key, ())
        labels = tuple(sorted(values))
        projected.append(
            entry if labels == entry.labels else replace(entry, labels=labels, revision=entry.revision.next())
        )
    return tuple(projected)


def patch_entry_states(
    entries: tuple[VariantEntryState, ...],
    states: Mapping[EntryKey, tuple[str, int]],
) -> tuple[VariantEntryState, ...]:
    normalized = {key: (str(translation), Stage(stage)) for key, (translation, stage) in states.items()}
    available = {entry.entry_key for entry in entries}
    if missing := set(normalized).difference(available):
        raise ValueError(f"translated entries are not present in the active Variant: {len(missing)}")

    projected: list[VariantEntryState] = []
    for entry in entries:
        state = normalized.get(entry.entry_key)
        if state is None or state == (entry.translation, entry.stage):
            projected.append(entry)
            continue
        projected.append(
            replace(
                entry,
                translation=state[0],
                stage=state[1],
                revision=entry.revision.next(),
            )
        )
    return tuple(projected)


def patch_entry_records(
    entries: tuple[VariantEntryState, ...],
    patches: Mapping[EntryKey, EntryStatePatch],
) -> tuple[VariantEntryState, ...]:
    """Patch translation, stage and external identity without touching source-owned text."""

    normalized = {
        key: EntryStatePatch(str(patch.translation), Stage(patch.stage), tuple(patch.external_refs))
        for key, patch in patches.items()
    }
    available = {entry.entry_key for entry in entries}
    if missing := set(normalized).difference(available):
        raise ValueError(f"patched entries are not present in the active Variant: {len(missing)}")

    projected: list[VariantEntryState] = []
    for entry in entries:
        patch = normalized.get(entry.entry_key)
        if patch is None:
            projected.append(entry)
            continue
        state = (patch.translation, patch.stage, patch.external_refs)
        if state == (entry.translation, entry.stage, entry.external_refs):
            projected.append(entry)
            continue
        projected.append(
            replace(
                entry,
                translation=patch.translation,
                stage=patch.stage,
                external_refs=patch.external_refs,
                revision=entry.revision.next(),
                inferred_fields=tuple(value for value in entry.inferred_fields if value != "external_refs"),
            )
        )
    return tuple(projected)


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
