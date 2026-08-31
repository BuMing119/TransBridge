from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

PLUGIN_PARENT_DIAL_FORMID = "plugin.parent_dial_formid"
PLUGIN_SOURCE_ORDER = "plugin.source_order"


class _EntryWithMetadata(Protocol):
    metadata: tuple[tuple[str, Any], ...]


def build_plugin_metadata(context: object | None, source_order: int | None) -> tuple[tuple[str, Any], ...]:
    """Build source-specific metadata without changing the public entry identity."""
    metadata: list[tuple[str, Any]] = []
    if source_order is not None:
        if type(source_order) is not int or source_order < 0:
            raise ValueError("source_order must be a non-negative integer")
        metadata.append((PLUGIN_SOURCE_ORDER, source_order))

    parent_dial_formid = getattr(context, "dialogue_topic", None)
    if isinstance(parent_dial_formid, str) and parent_dial_formid:
        metadata.append((PLUGIN_PARENT_DIAL_FORMID, parent_dial_formid))

    return tuple(sorted(metadata))


def plugin_source_order(entry: _EntryWithMetadata) -> int | None:
    """Return a valid plugin source order, or None for legacy/malformed metadata."""
    value = dict(entry.metadata).get(PLUGIN_SOURCE_ORDER)
    return value if type(value) is int and value >= 0 else None


def restore_plugin_source_order[EntryT: _EntryWithMetadata](entries: Iterable[EntryT]) -> list[EntryT]:
    """Restore source order only when every entry has valid plugin metadata."""
    ordered_entries = list(entries)
    source_orders = [plugin_source_order(entry) for entry in ordered_entries]
    if any(source_order is None for source_order in source_orders) or len(set(source_orders)) != len(source_orders):
        return ordered_entries
    return [
        entry
        for _, entry in sorted(
            zip(source_orders, ordered_entries, strict=True),
            key=lambda item: item[0],
        )
    ]
