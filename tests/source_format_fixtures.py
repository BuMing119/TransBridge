"""Small complete translation files shared by I/O and project lifecycle tests."""

import json
from pathlib import Path
import struct

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def write_translation_source(root: Path, kind: str) -> Path:
    path = root / ("translated.json" if kind.startswith("json.") else "translated.sst")
    if kind == "json.transbridge":
        TranslationEntryCollection([TranslationEntry("Npc:800", "npc", "Hello", "你好", 1, "NPC_:FULL")]).to_json_file(
            path
        )
    elif kind == "json.dsd":
        path.write_text(
            json.dumps([{"form_id": "00000800|Test.esp", "type": "NPC_ FULL", "original": "Hello", "string": "你好"}]),
            encoding="utf-8",
        )
    elif kind == "binary.eet":
        from tests.eet_fixtures import write_eet

        return write_eet(root / "translated.eet")
    else:
        original, translated = "Hello".encode("utf-16-le"), "你好".encode("utf-16-le")
        tail = struct.pack("<I", len(translated)) + translated
        if kind == "sst.ssu8":
            header = b"SSU8" + b"\0" * 5
        elif kind == "sst.ssu9":
            header = b"SSU9" + b"\0" * 9
        else:
            raise ValueError(kind)
        data = (
            header
            + struct.pack("<BiI4s4sHHIBB", 0, 1, 0x800, b"NPC_", b"FULL", 0, 0, 0, 0, 1)
            + struct.pack("<i", len(original))
            + original
            + tail
        )
        path.write_bytes(data)
    return path
