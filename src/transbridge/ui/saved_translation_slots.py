"""Editable saved Variant rows for which no source hydration is available."""

from collections import defaultdict

from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance
from transbridge.application.preflight.inputs import SOURCE_CONTEXT_KNOWN
from transbridge.application.projections.models import copy_projection_value
from transbridge.application.projects.source_content import source_content_identity
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.source_input_state import ORIGINAL_KNOWN


def saved_translation_slots(snapshot, hydrated_slots, *, states=None):
    """Keep every saved identity visible without manufacturing a source snapshot."""
    values = {} if snapshot is None else snapshot.values
    covered = {entry.identity for _, slot in hydrated_slots for entry in slot.collection}
    groups = defaultdict(list)
    states = (
        ((EntryKey.from_dict(dict(row["entry_key"])), row) for row in values.get("entries", ()))
        if states is None
        else states
    )
    for key, row in states:
        if key not in covered and not row.get("tombstone", False):
            groups[key.namespace.value].append((key, copy_projection_value(row)))
    used = {key for key, _ in hydrated_slots}
    result = []
    for namespace, rows in groups.items():
        matches = [source for source in values.get("sources", ()) if source_content_identity(source) == namespace]
        source = matches[0] if len(matches) == 1 else {}
        missing = next((item for item in values.get("missing_inputs", ()) if item.get("namespace") == namespace), {})
        source_id = source.get("source_id") or missing.get("source_id")
        slot_key = source_id or namespace
        if slot_key in used:
            slot_key = f"missing:{namespace}"
        used.add(slot_key)
        format_value = source.get("format_id") or missing.get("format_id")
        entries = tuple(
            TranslationEntry(
                id=key.serialize() if key.original is not None else key.local_key,
                key=key.local_key,
                original=key.original if key.original is not None else "",
                translation=str(row.get("translation", "")),
                stage=int(row.get("stage", 0)),
                context=None,
                entry_key=key,
                revision=EntryRevision(int(row.get("revision", 0))),
                external_refs=tuple(ExternalEntryRef.from_dict(dict(value)) for value in row.get("external_refs", ())),
                provenance=tuple(Provenance.from_dict(dict(value)) for value in row.get("provenance", ())),
                metadata=((ORIGINAL_KNOWN, key.original is not None), (SOURCE_CONTEXT_KNOWN, False)),
            )
            for key, row in rows
        )
        result.append((
            slot_key,
            CollectionSlot(
                label=str(source.get("display_name") or missing.get("display_name") or "未对应的保存译文"),
                collection=TranslationEntryCollection(entries),
                format_id=FormatId(format_value) if format_value else None,
                source_id=source_id,
                source_namespace=namespace,
            ),
        ))
    return tuple(result)
