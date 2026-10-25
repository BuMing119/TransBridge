"""Read unchanged output values from the captured source for fidelity checks."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .contracts import FormatId, WriteRequest


def source_entry_texts(request: WriteRequest) -> dict[str, tuple[str, str]]:
    """Index protected output values once, without reopening the mutable source."""
    snapshot = request.source_snapshot
    if snapshot is None or snapshot.content is None:
        raise ValueError("preserving source content requires a captured source snapshot")
    if request.format_id in {FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS}:
        from .strings_adapter import _snapshot_records

        records = _snapshot_records(snapshot)
        values = {f"strings:{record.string_id}": (record.text, "") for record in records}
        if len(values) != len(records):
            raise ValueError("protected strings have duplicate source records")
        return values
    if request.format_id in {FormatId.XML_EET, FormatId.XML_XT}:
        from .legacy_adapters import _eet_nodes, _xt_nodes

        root = ET.fromstring(snapshot.content)
        nodes = _eet_nodes(root) if request.format_id is FormatId.XML_EET else _xt_nodes(root)
        tag = "TRADUIT" if request.format_id is FormatId.XML_EET else "Dest"
        original_tag = "ORIGINAL" if request.format_id is FormatId.XML_EET else "Source"
        return {
            key: (matches[0].findtext(original_tag, "") or "", matches[0].findtext(tag, "") or "")
            for key, matches in nodes.items()
            if len(matches) == 1
        }
    raise ValueError("source preservation is not supported by this exchange format")
