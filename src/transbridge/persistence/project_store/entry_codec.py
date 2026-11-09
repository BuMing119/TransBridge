# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Encode typed source columns and one authoritative Variant entry state."""

from __future__ import annotations

import json
from typing import Any

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.projects.source_fields import business_fields, entry_fields
from transbridge.application.projects.storage_contracts import StoredEntry
from transbridge.persistence.v2.variant import VariantEntryState

STATE_FIELDS = "translation, stage, labels, provenance, revision, tombstone, inferred_fields, external_refs"
EFFECTIVE_STATE_FIELDS = ", ".join(f"COALESCE(vs.{field}, ds.{field}) AS {field}" for field in STATE_FIELDS.split(", "))
DEFAULT_STATE_JOIN = "LEFT JOIN entry_default AS ds ON ds.entry_id = e.id"
VISIBLE_STATE = "COALESCE(vs.tombstone, ds.tombstone, 0) = 0"


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":"))


def pack_text(value: str) -> str:
    """SQLite's UTF-8 binding cannot encode isolated UTF-16 surrogates from SST."""
    if value.startswith("\x01") or any("\ud800" <= character <= "\udfff" for character in value):
        return "\x01" + encode_json(value)
    return value


def unpack_text(value: str) -> str:
    return json.loads(value[1:]) if value.startswith("\x01") else value


def immutable_json(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(immutable_json(item) for item in value)
    return value


def entry_values(entry: StoredEntry) -> tuple[Any, ...]:
    snapshot = entry.snapshot
    key = snapshot.entry_key
    return (
        pack_text(key.local_key),
        pack_text(key.original or ""),
        int(key.original is not None),
        pack_text(snapshot.legacy_id),
        pack_text(snapshot.original),
        None if snapshot.context is None else pack_text(snapshot.context),
        snapshot.string_id,
        encode_json(entry_fields(snapshot.metadata)),
    )


def state_values(state: VariantEntryState) -> tuple[Any, ...]:
    return (
        pack_text(state.translation),
        state.stage.value,
        encode_json(state.labels),
        encode_json([_business_item(item) for item in state.provenance]),
        state.revision.value,
        int(state.tombstone),
        encode_json(state.inferred_fields),
        encode_json([_business_item(item) for item in state.external_refs]),
    )


def decode_state(row, key: EntryKey) -> VariantEntryState:
    if row["stage"] is None:
        return VariantEntryState(key)
    return VariantEntryState(
        key,
        unpack_text(row["translation"]),
        row["stage"],
        _read_list(row["labels"]),
        tuple(Provenance.from_dict(item) for item in _read_list(row["provenance"])),
        EntryRevision(row["revision"]),
        bool(row["tombstone"]),
        _read_list(row["inferred_fields"]),
        tuple(ExternalEntryRef.from_dict(item) for item in _read_list(row["external_refs"])),
    )


def decode_key(row) -> EntryKey:
    return EntryKey(
        SourceNamespace(row["namespace"]),
        unpack_text(row["local_key"]),
        unpack_text(row["identity_original"]) if row["has_identity_original"] else None,
    )


def decode_entry(row) -> StoredEntry:
    key = decode_key(row)
    state = decode_state(row, key)
    return StoredEntry(
        EntrySnapshot(
            key,
            unpack_text(row["legacy_id"]),
            unpack_text(row["original"]),
            state.translation,
            state.stage.value,
            None if row["context"] is None else unpack_text(row["context"]),
            state.external_refs,
            state.revision,
            state.provenance,
            _read_metadata(row["metadata"]),
            row["string_id"],
            key.original is not None,
        ),
        state,
    )


def _read_list(value: str) -> tuple:
    return () if value == "[]" else tuple(json.loads(value))


def _read_metadata(value: str) -> tuple:
    return () if value == "{}" else tuple((key, immutable_json(item)) for key, item in json.loads(value).items())


def _business_item(item: ExternalEntryRef | Provenance) -> dict[str, Any]:
    data = item.to_dict()
    data["metadata"] = business_fields(item.metadata)
    return data
