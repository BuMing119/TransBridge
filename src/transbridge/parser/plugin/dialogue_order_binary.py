"""Small, bounds-checked record reader shared by both dialogue evidence inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import struct
import zlib

from sse_plugin_interface.group import Group
from sse_plugin_interface.record import Record


class DialogueOrderCancelled(Exception):
    """The caller cancelled evidence extraction."""


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    kind: str
    form_id: str
    ordinal: int
    deleted: bool
    parent: str
    fields: tuple[tuple[int, str, bytes], ...] = ()


RELEVANT = frozenset({"QUST", "DIAL", "INFO", "DLBR"})
REFERENCE_FIELDS = frozenset({"QNAM", "BNAM", "SNAM", "TCLT"})
# A live kind, an empty string for deleted records, or None for duplicates.
# Unrelated records need no ordinal, parent, payload, or per-record container.
RecordIdentities = dict[str, str | None]


def remember_identity(identities: RecordIdentities, form_id: str, kind: str, deleted: bool) -> None:
    identities[form_id] = None if form_id in identities else "" if deleted else kind


def check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise DialogueOrderCancelled("Dialogue relation extraction was cancelled")


def reference_fields(payload: bytes) -> tuple[tuple[int, str, bytes], ...]:
    """Retain physical subrecord indices, including XXXX headers in the count."""
    position, index, extended = 0, 0, None
    result = []
    while position < len(payload):
        if position + 6 > len(payload):
            raise ValueError(f"Truncated dialogue subrecord header at {position}")
        name = payload[position : position + 4].decode("ascii")
        size = struct.unpack_from("<H", payload, position + 4)[0]
        position += 6
        if name == "XXXX":
            if size != 4 or extended is not None or position + 4 > len(payload):
                raise ValueError("Invalid extended dialogue subrecord size")
            extended = struct.unpack_from("<I", payload, position)[0]
        elif extended is not None:
            size, extended = extended, None
        if position + size > len(payload):
            raise ValueError(f"Dialogue subrecord {name} exceeds its record")
        if name in REFERENCE_FIELDS:
            result.append((index, name, payload[position : position + size]))
        position += size
        index += 1
    if extended is not None:
        raise ValueError("Dangling extended dialogue subrecord size")
    return tuple(result)


def parsed_records(
    groups: Iterable,
    cancelled: Callable[[], bool] | None = None,
    *,
    identities: RecordIdentities | None = None,
) -> Iterator[EvidenceRecord]:
    """Yield all records, or only relevant records while filling an identity index."""
    ordinal = 0  # TES4 is the first physical record.

    def visit(children, parent=""):
        nonlocal ordinal
        for item in children:
            check_cancelled(cancelled)
            if isinstance(item, Group):
                yield from visit(item.children, item.label.upper() if item.group_type == 7 else parent)
            elif isinstance(item, Record):
                ordinal += 1
                kind, form_id, deleted = item.type, item.formid.upper(), bool(int(item.flags) & 0x20)
                if identities is not None:
                    remember_identity(identities, form_id, kind, deleted)
                    if kind not in RELEVANT:
                        continue
                yield EvidenceRecord(
                    kind,
                    form_id,
                    ordinal,
                    deleted,
                    parent if kind == "INFO" else "",
                    reference_fields(item.data) if kind in RELEVANT else (),
                )

    yield from visit(groups)


def binary_records(
    content: bytes,
    cancelled: Callable[[], bool] | None = None,
    *,
    identities: RecordIdentities | None = None,
) -> Iterator[EvidenceRecord]:
    """Skip unrelated payloads without decoding strings or constructing a plugin.

    When identities is supplied, index every identity and only yield the four
    relevant record kinds, avoiding temporary objects for all other records.
    """
    if content[:4] != b"TES4":
        raise ValueError("Dialogue evidence requires a TES4 source snapshot")
    position, ordinal = 0, 0
    groups: list[tuple[int, str]] = []
    kinds = {b"GRUP": "GRUP"}
    while position < len(content):
        check_cancelled(cancelled)
        while groups and position == groups[-1][0]:
            groups.pop()
        limit = groups[-1][0] if groups else len(content)
        if position + 24 > limit:
            raise ValueError(f"Truncated plugin record header at {position}")
        kind_bytes = content[position : position + 4]
        kind = kinds.get(kind_bytes)
        if kind is None:
            kind = kinds[kind_bytes] = kind_bytes.decode("ascii")
        size = struct.unpack_from("<I", content, position + 4)[0]
        if kind == "GRUP":
            if size < 24 or position + size > limit:
                raise ValueError(f"Invalid plugin group extent at {position}")
            group_type = struct.unpack_from("<i", content, position + 12)[0]
            parent = groups[-1][1] if groups else ""
            if group_type == 7:
                parent = f"{struct.unpack_from('<I', content, position + 8)[0]:08X}"
            groups.append((position + size, parent))
            position += 24
            continue
        end = position + 24 + size
        if end > limit:
            raise ValueError(f"Plugin record {kind} exceeds its containing group")
        flags, form_id = struct.unpack_from("<II", content, position + 8)
        form_id_text, deleted = f"{form_id:08X}", bool(flags & 0x20)
        if identities is not None:
            remember_identity(identities, form_id_text, kind, deleted)
            if kind not in RELEVANT:
                ordinal += 1
                position = end
                continue
        fields = ()
        if kind in RELEVANT:
            payload = content[position + 24 : end]
            if flags & 0x40000:
                if len(payload) < 4:
                    raise ValueError("Truncated compressed dialogue record")
                expected = struct.unpack_from("<I", payload)[0]
                try:
                    payload = zlib.decompress(payload[4:])
                except zlib.error as exc:
                    raise ValueError(f"Invalid compressed dialogue record {form_id:08X}") from exc
                if len(payload) != expected:
                    raise ValueError(f"Incorrect decoded dialogue record size for {form_id:08X}")
            fields = reference_fields(payload)
        yield EvidenceRecord(
            kind,
            form_id_text,
            ordinal,
            deleted,
            groups[-1][1] if groups and kind == "INFO" else "",
            fields,
        )
        ordinal += 1
        position = end
    if groups and any(end != position for end, _ in groups):
        raise ValueError("Plugin ends before its containing group")
