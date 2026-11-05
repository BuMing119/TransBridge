"""Retain registered plugin terminology scope across immutable source hydration."""

from dataclasses import replace
from pathlib import PureWindowsPath

from transbridge.application.io import FormatId


def scoped_source_entries(entries, format_id, location, *, plugin_scope=None):
    """Preserve explicit scope, then qualified record ownership, then source identity."""
    if format_id is not FormatId.PLUGIN_SSE:
        return tuple(entries)
    fallback = plugin_scope or PureWindowsPath(location).name
    output = []
    for entry in entries:
        metadata = dict(entry.metadata)
        configured = metadata.get("terminology_plugin_id")
        if isinstance(configured, str) and configured.strip():
            output.append(entry)
            continue
        qualified = getattr(entry, "form_id_with_plugin", None)
        owner = qualified.rsplit("|", 1)[1].strip() if isinstance(qualified, str) and "|" in qualified else None
        owner = owner or fallback
        if owner:
            metadata["terminology_plugin_id"] = owner
            output.append(replace(entry, metadata=tuple(metadata.items())))
        else:
            output.append(entry)
    return tuple(output)
