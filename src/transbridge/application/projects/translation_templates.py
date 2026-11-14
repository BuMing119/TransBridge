# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Reconstruct XML and localized text templates from approved translation fields."""

from __future__ import annotations

import codecs
from collections.abc import Sequence
import xml.etree.ElementTree as ET

from transbridge.application.io.contracts import FormatId
from transbridge.parser.localized_string_records import LocalizedStringRecord, encode_chunk, render

from .storage_contracts import StoredEntry

XML_FORMATS = frozenset({FormatId.XML_EET, FormatId.XML_XT})
STRINGS_FORMATS = frozenset({FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS})


def extract_xml_records(content: bytes) -> tuple[tuple, tuple[tuple[str, str | None], ...]]:
    root = ET.fromstring(content, parser=_xml_parser())
    records = []
    for item in root:
        tail, item.tail = item.tail, None
        records.append((ET.tostring(item, encoding="unicode"), tail))
    return (root.tag, tuple(root.attrib.items()), root.text), tuple(records)


def rebuild_xml(header: tuple, records: tuple[tuple[str, str | None], ...], encoding: str, bom: bytes) -> bytes:
    tag, attrs, text = header
    root = ET.Element(tag, dict(attrs))
    root.text = text
    for fragment, tail in records:
        wrapper = ET.fromstring(f"<record>{fragment}</record>", parser=_xml_parser())
        if len(wrapper) != 1:
            raise ValueError("invalid XML physical translation record")
        item = wrapper[0]
        item.tail = tail
        root.append(item)
    codec = codecs.lookup(encoding).name
    declaration_encoding = "utf-16" if codec.startswith("utf-16") else codec
    data = (
        f'<?xml version="1.0" encoding="{declaration_encoding}"?>\n' + ET.tostring(root, encoding="unicode")
    ).encode(codec)
    return bom + data if bom and not data.startswith(bom) else data


def rebuild_strings(
    format_id: FormatId, entries: Sequence[StoredEntry]
) -> tuple[bytes, tuple[LocalizedStringRecord, ...]]:
    length_prefixed = format_id is not FormatId.STRINGS
    chunks = []
    records = []
    offset = 0
    payload_start = 4 if length_prefixed else 0
    for index, entry in enumerate(entries):
        item = entry.snapshot
        if item.string_id is None:
            raise ValueError("localized translation record is missing its String ID")
        encoding = dict(item.metadata)["strings.encoding"]
        chunk = encode_chunk(item.original, encoding, length_prefixed)
        chunks.append((item.string_id, chunk))
        # Existing projects may have saved different per-entry codecs. Preserve
        # the known Unicode facts without attempting to decode template bytes.
        payload = chunk[payload_start:-1]
        records.append(LocalizedStringRecord(index, item.string_id, offset, payload, chunk, item.original, encoding))
        offset += len(chunk)
    return render(chunks), tuple(records)


def _xml_parser() -> ET.XMLParser:
    return ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
