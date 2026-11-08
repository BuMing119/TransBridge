"""Recover known EditorIDs for conservative EET migrations after hydration."""


def known_editor_id(entry) -> str:
    metadata = dict(entry.metadata)
    value = metadata.get("eet.editor_id") or entry.editor_id
    if not value and metadata.get("io.format") == "plugin.sse":
        # Plugin EntrySnapshot retains the canonical legacy record locator,
        # even when its convenience editor_id field is no longer projected.
        value = entry.id.partition(":")[0] if ":" in entry.id else ""
    return value.casefold() if isinstance(value, str) else ""
