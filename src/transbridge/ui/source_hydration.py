"""Build legacy workbench projections from authoritative source hydration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import PureWindowsPath

from transbridge.application.io import FormatId
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.entry_projection import EntryProjectionUpdate
from transbridge.ui.projection_types import CollectionSlot


def collection_from_hydration(source, *, projection_update=None) -> TranslationEntryCollection:
    def entries():
        for item in source.entries:
            if projection_update is not None and projection_update.is_tombstone(item.entry_key):
                continue
            values = {} if projection_update is None else projection_update.projected_values(item.entry_key, item)
            yield TranslationEntry(
                id=item.legacy_id,
                key=item.entry_key.local_key,
                original=item.original,
                translation=values.get("translation", item.translation),
                stage=values.get("stage", item.stage),
                context=item.context,
                entry_key=item.entry_key,
                external_refs=values.get("external_refs", item.external_refs),
                revision=values.get("revision", item.revision),
                provenance=values.get("provenance", item.provenance),
                metadata=item.metadata,
                string_id=item.string_id,
            )

    return TranslationEntryCollection(entries())


def slot_from_hydration(source, *, plugin=None, projection_update=None) -> CollectionSlot:
    format_id = source.format_id
    reference = getattr(source, "reference_location", None)
    reference = source.location if reference is None else reference
    return CollectionSlot(
        label=PureWindowsPath(source.location).stem,
        collection=collection_from_hydration(source, projection_update=projection_update),
        esp_path=reference or None if format_id is FormatId.PLUGIN_SSE else None,
        eet_path=reference or None if format_id is FormatId.XML_EET else None,
        xt_path=reference or None if format_id is FormatId.XML_XT else None,
        plugin=plugin,
        source_snapshot=source.source_snapshot,
        format_id=format_id,
        source_id=getattr(source, "source_id", None),
        source_namespace=source.entries[0].entry_key.namespace.value if source.entries else None,
    )


def prepare_project_slots(hydrations, snapshot) -> tuple[tuple[str, CollectionSlot], ...]:
    """Build detached slots in a worker with one index of the whole Variant.

    Ownership transfers to the GUI when installed. The producing worker must
    not retain or mutate these collections after returning the bundle.
    """
    update = EntryProjectionUpdate(() if snapshot is None else snapshot.values.get("entries", ()))
    slots = []
    identities = set()
    values = {} if snapshot is None else snapshot.values
    registered = authoritative_baseline_sources(
        tuple(values.get("sources", ())), tuple(values.get("source_relations", ()))
    )
    for source in hydrations:
        namespace = source.entries[0].entry_key.namespace.value if source.entries else None
        source_id = getattr(source, "source_id", None)
        if source_id:
            matches = [item for item in registered if item["source_id"] == source_id]
        elif namespace:
            matches = [item for item in registered if source_content_identity(item) == namespace]
        else:
            fingerprint = getattr(source, "fingerprint", None)
            matches = [item for item in registered if fingerprint and item.get("fingerprint") == fingerprint]
        registration = matches[0] if len(matches) == 1 else None
        if registration is not None:
            source = replace(
                source, source_id=registration["source_id"], reference_location=registration.get("location") or ""
            )
        key = source_slot_key(source)
        if key in identities:
            raise ValueError(f"Duplicate project source identity: {key}")
        identities.add(key)
        slots.append((key, slot_from_hydration(source, projection_update=update)))
    from transbridge.ui.saved_translation_slots import saved_translation_slots

    slots.extend(saved_translation_slots(snapshot, slots, states=update.saved_rows()))
    return tuple(slots)


def source_slot_key(source) -> str:
    """Keep view identity independent of an optional historical path."""
    if getattr(source, "source_id", None):
        return source.source_id
    if source.entries:
        return source.entries[0].entry_key.namespace.value
    return source.source_snapshot.sha256


def apply_variant_projection(collection: TranslationEntryCollection, states) -> TranslationEntryCollection:
    update = EntryProjectionUpdate(states)
    # Preserve the public contract of fresh collections and matching entries.
    # Editor commits opt into identity reuse through update.collection.
    return TranslationEntryCollection(
        update.entry(entry, reuse_unchanged=False) for entry in collection if not update.is_tombstone(entry.identity)
    )


__all__ = ["apply_variant_projection", "collection_from_hydration", "prepare_project_slots", "slot_from_hydration"]
