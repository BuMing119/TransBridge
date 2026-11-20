# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Identify plugin records and their editable fields without depending on Qt."""

from __future__ import annotations

from dataclasses import dataclass
import re

from transbridge.application.io.identity import SourceNamespace
from transbridge.converter.plugin_entry_metadata import restore_plugin_source_order

_LEGACY_KEY = re.compile(
    r"^(?P<editor>.*):(?P<form>[0-9A-Fa-f]{8})\|[^~]+~(?P<record>[A-Za-z0-9_]{4}):(?P<field>[^|]+)"
)
_SIGNATURE = re.compile(r"[A-Z0-9_]{4}")
_FORM_ID = re.compile(r"[0-9A-F]{8}")


@dataclass(frozen=True, slots=True)
class RecordIdentity:
    namespace: SourceNamespace
    signature: str
    form_id: str


def _legacy(entry):
    return _LEGACY_KEY.match(entry.identity.local_key)


def _context(entry):
    context = (entry.context or getattr(entry, "dsd_type", "") or "").partition("|")[0]
    return context.replace(" ", ":").partition(":")


def record_identity(entry) -> RecordIdentity | None:
    """Keep raw FormIDs within their source and record signature."""
    metadata = dict(entry.metadata)
    legacy = _legacy(entry)
    signature = metadata.get("plugin.record_type") or _context(entry)[0] or (legacy["record"] if legacy else "")
    form = metadata.get("plugin.form_id") or getattr(entry, "form_id_with_plugin", None)
    form = form or (legacy["form"] if legacy else "")
    signature = str(signature).upper()
    form = str(form).partition("|")[0].upper()
    if not _SIGNATURE.fullmatch(signature) or not _FORM_ID.fullmatch(form):
        return None
    return RecordIdentity(entry.identity.namespace, signature, form)


def record_editor_id(entry) -> str:
    metadata = dict(entry.metadata)
    legacy = _legacy(entry)
    value = metadata.get("plugin.editor_id") or getattr(entry, "editor_id", "") or (legacy["editor"] if legacy else "")
    return str(value) if value and str(value).casefold() not in {"none", "null"} else ""


def record_field_id(entry) -> str:
    metadata = dict(entry.metadata)
    legacy = _legacy(entry)
    return str(metadata.get("plugin.field_type") or _context(entry)[2] or (legacy["field"] if legacy else ""))


def find_record_entries(collection, selected_entry) -> tuple:
    """Return every field of one record, retaining repeated fields and source order."""
    identity = record_identity(selected_entry)
    if identity is None:
        return ()
    lookup = getattr(collection, "record_entries", None)
    if callable(lookup):
        return tuple(lookup(selected_entry))
    return tuple(
        restore_plugin_source_order(
            entry
            for entry in collection
            if not getattr(entry, "tombstone", False) and record_identity(entry) == identity
        )
    )
