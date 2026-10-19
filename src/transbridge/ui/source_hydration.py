"""Build legacy workbench projections from authoritative source hydration."""

from __future__ import annotations

from pathlib import Path

from transbridge.application.io import FormatId
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.entry_projection import EntryProjectionUpdate
from transbridge.ui.projection_types import CollectionSlot


def collection_from_hydration(source, *, projection_update=None) -> TranslationEntryCollection:
    def entries():
        for item in source.entries:
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
    return CollectionSlot(
        label=Path(source.location).stem,
        collection=collection_from_hydration(source, projection_update=projection_update),
        esp_path=source.location if format_id is FormatId.PLUGIN_SSE else None,
        eet_path=source.location if format_id is FormatId.XML_EET else None,
        xt_path=source.location if format_id is FormatId.XML_XT else None,
        plugin=plugin,
        source_snapshot=source.source_snapshot,
        format_id=format_id,
    )


def prepare_project_slots(hydrations, snapshot) -> tuple[tuple[str, CollectionSlot], ...]:
    """Build detached slots in a worker with one index of the whole Variant.

    Ownership transfers to the GUI when installed. The producing worker must
    not retain or mutate these collections after returning the bundle.
    """
    update = EntryProjectionUpdate(() if snapshot is None else snapshot.values.get("entries", ()))
    slots = []
    locations = set()
    for source in hydrations:
        if source.location in locations:
            raise ValueError(f"Duplicate project source location: {source.location}")
        locations.add(source.location)
        slots.append((source.location, slot_from_hydration(source, projection_update=update)))
    return tuple(slots)


def apply_variant_projection(collection: TranslationEntryCollection, states) -> TranslationEntryCollection:
    update = EntryProjectionUpdate(states)
    # Preserve the public contract of fresh collections and matching entries.
    # Editor commits opt into identity reuse through update.collection.
    return TranslationEntryCollection(update.entry(entry, reuse_unchanged=False) for entry in collection)


__all__ = ["apply_variant_projection", "collection_from_hydration", "prepare_project_slots", "slot_from_hydration"]
