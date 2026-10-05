"""Build legacy workbench projections from authoritative source hydration."""

from __future__ import annotations

from pathlib import Path

from transbridge.application.io import FormatId
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.entry_projection import EntryProjectionUpdate
from transbridge.ui.projection_types import CollectionSlot


def collection_from_hydration(source) -> TranslationEntryCollection:
    return TranslationEntryCollection(
        tuple(
            TranslationEntry(
                id=item.legacy_id,
                key=item.entry_key.local_key,
                original=item.original,
                translation=item.translation,
                stage=item.stage,
                context=item.context,
                entry_key=item.entry_key,
                external_refs=item.external_refs,
                revision=item.revision,
                provenance=item.provenance,
                metadata=item.metadata,
                string_id=item.string_id,
            )
            for item in source.entries
        )
    )


def slot_from_hydration(source, *, plugin=None) -> CollectionSlot:
    format_id = source.format_id
    return CollectionSlot(
        label=Path(source.location).stem,
        collection=collection_from_hydration(source),
        esp_path=source.location if format_id is FormatId.PLUGIN_SSE else None,
        eet_path=source.location if format_id is FormatId.XML_EET else None,
        xt_path=source.location if format_id is FormatId.XML_XT else None,
        plugin=plugin,
        source_snapshot=source.source_snapshot,
        format_id=format_id,
    )


def apply_variant_projection(collection: TranslationEntryCollection, states) -> TranslationEntryCollection:
    update = EntryProjectionUpdate(states)
    # Preserve the public contract of fresh collections and matching entries.
    # Editor commits opt into identity reuse through update.collection.
    return TranslationEntryCollection(update.entry(entry, reuse_unchanged=False) for entry in collection)


__all__ = ["apply_variant_projection", "collection_from_hydration", "slot_from_hydration"]
