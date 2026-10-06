"""Response IDs for AI protocols without changing source entry key/id facades."""

from __future__ import annotations

from transbridge.application.io.identity import EntryKey


def ai_entry_id(entry: object) -> str:
    """Qualify ambiguous source IDs with their complete canonical identity."""
    identity = getattr(entry, "identity", None)
    if isinstance(identity, EntryKey) and identity.original is not None:
        return identity.serialize()
    value = getattr(entry, "id", getattr(entry, "key", ""))
    return "" if value is None else str(value)


def ai_entry_key(entry: object) -> str:
    """Qualify ambiguous source keys while keeping ordinary response IDs stable."""
    identity = getattr(entry, "identity", None)
    if isinstance(identity, EntryKey) and identity.original is not None:
        return identity.serialize()
    value = getattr(entry, "key", "")
    return "" if value is None else str(value)
