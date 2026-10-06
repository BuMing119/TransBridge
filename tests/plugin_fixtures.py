"""Minimal real plugin binaries for parser and import contract tests."""

from pathlib import Path
import struct


def _field(kind: bytes, text: str) -> bytes:
    data = text.encode("utf-8") + b"\x00"
    return kind + struct.pack("<H", len(data)) + data


def _record(kind: bytes, form_id: int, data: bytes) -> bytes:
    return struct.pack("<4sIIIHHHH", kind, len(data), 0, form_id, 0, 0, 44, 0) + data


def write_plugin(path: Path, records: list[tuple[int, str, str]]) -> Path:
    """Write TES4/NPC_ data from ``(form_id, editor_id, text)`` records."""
    data = b"".join(
        _record(b"NPC_", form_id, _field(b"EDID", editor_id) + _field(b"FULL", text))
        for form_id, editor_id, text in records
    )
    group = struct.pack("<4sI4siHHI", b"GRUP", 24 + len(data), b"NPC_", 0, 0, 0, 0) + data
    path.write_bytes(_record(b"TES4", 0, b"") + group)
    return path
