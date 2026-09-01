"""Build legacy workbench projections from authoritative source hydration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryRevision, ExternalEntryRef, Provenance
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
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
    projected = {(item["entry_key"]["namespace"], item["entry_key"]["local_key"]): item for item in states}

    def apply(entry: TranslationEntry) -> TranslationEntry:
        state = projected.get((entry.identity.namespace.value, entry.identity.local_key))
        if state is None:
            return entry
        inferred = set(str(value) for value in state.get("inferred_fields", ()))
        external_refs = (
            entry.external_refs
            if "external_refs" in inferred or "external_refs" not in state
            else tuple(ExternalEntryRef.from_dict(value) for value in state.get("external_refs", ()))
        )
        return replace(
            entry,
            translation=str(state.get("translation", "")),
            stage=int(state.get("stage", 0)),
            external_refs=external_refs,
            revision=(entry.revision if "revision" not in state else EntryRevision(int(state["revision"]))),
            provenance=(
                entry.provenance
                if "provenance" not in state
                else tuple(Provenance.from_dict(value) for value in state.get("provenance", ()))
            ),
        )

    return TranslationEntryCollection(apply(entry) for entry in collection)


__all__ = ["apply_variant_projection", "collection_from_hydration", "slot_from_hydration"]
