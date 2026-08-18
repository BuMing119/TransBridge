from __future__ import annotations

import json
from pathlib import Path

from transbridge.application.io import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from transbridge.smart_assistant.file_parser.paratranz_parser import ParatranzParser


def test_smart_assistant_legacy_parser_delegates_to_v2_mapping(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    source.write_text(
        '[{"id":null,"key":"dialogue","original":"Hello","translation":"你好","stage":1}]',
        encoding="utf-8",
    )

    document = ParatranzParser().parse(source)

    assert document.format == "paratranz"
    assert document.metadata["outcome"] == "completed"
    assert document.sections[0]["entries"] == [
        {"key": "dialogue", "original": "Hello", "id": None, "translation": "你好", "stage": 1}
    ]


def test_categorized_export_uses_local_key_and_only_real_external_id(tmp_path: Path) -> None:
    namespace = SourceNamespace("fixture:categorized")
    with_id = TranslationEntry(
        "legacy-must-not-be-exported",
        "stable-business-key",
        "Auri",
        "奥里",
        1,
        "NPC_:FULL",
        entry_key=EntryKey(namespace, "stable-business-key"),
        external_refs=(ExternalEntryRef("paratranz", "offline", 73),),
    )
    without_id = TranslationEntry(
        "another-legacy-id",
        "stable-without-remote",
        "Bandit",
        "强盗",
        1,
        "NPC_:FULL",
        entry_key=EntryKey(namespace, "stable-without-remote"),
    )

    export_to_categorized_json_files(TranslationEntryCollection((with_id, without_id)), tmp_path)

    outputs = tuple(tmp_path.glob("*.json"))
    assert len(outputs) == 1
    payload = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert payload[0]["key"] == "stable-business-key"
    assert payload[0]["id"] == 73
    assert payload[1]["key"] == "stable-without-remote"
    assert "id" not in payload[1]
    assert all("schema_version" not in item and "entry_key" not in item for item in payload)
