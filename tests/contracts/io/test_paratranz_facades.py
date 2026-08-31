from __future__ import annotations

import json
from pathlib import Path

from transbridge.application.io import EntryKey, ExternalEntryRef, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from transbridge.smart_assistant.file_parser.paratranz_parser import ParatranzParser


def test_paratranz_public_package_keeps_legacy_exports_after_lazy_loading() -> None:
    import transbridge.paratranz as package

    expected = {
        "ParatranzClient",
        "ParatranzConfig",
        "ParatranzProjectAPI",
        "ParatranzFilesAPI",
        "ParatranzStringsAPI",
        "ParatranzTermsAPI",
        "ParatranzMembersAPI",
        "ParatranzHistoryAPI",
        "ParatranzExportAPI",
        "ParatranzIssuesAPI",
        "ParatranzScoresAPI",
        "ParatranzMailsAPI",
        "ParatranzUserAPI",
        "ParaTranzUploader",
        "UploadResult",
        "ParaTranzDownloader",
        "DownloadResult",
        "ArtifactWorkflow",
    }

    assert set(package.__all__) == expected
    assert all(getattr(package, name, None) is not None for name in expected)


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


def _dialogue_entry(key: str, source_order: object | None) -> TranslationEntry:
    metadata = () if source_order is None else (("plugin.source_order", source_order),)
    return TranslationEntry(key, key, key, "", 0, "INFO:NAM1|00ABCDEF", metadata=metadata)


def test_categorized_dialogue_export_restores_complete_plugin_source_order(tmp_path: Path) -> None:
    collection = TranslationEntryCollection((
        _dialogue_entry("third", 2),
        _dialogue_entry("first", 0),
        _dialogue_entry("second", 1),
    ))

    export_to_categorized_json_files(collection, tmp_path)

    output = next(tmp_path.glob("*.json"))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["key"] for item in payload] == ["first", "second", "third"]
    assert all(not any(key.startswith("plugin.") for key in item) for item in payload)


def test_categorized_dialogue_export_keeps_collection_order_for_incomplete_metadata(tmp_path: Path) -> None:
    collection = TranslationEntryCollection((
        _dialogue_entry("third", 2),
        _dialogue_entry("legacy", None),
        _dialogue_entry("first", 0),
    ))

    export_to_categorized_json_files(collection, tmp_path)

    output = next(tmp_path.glob("*.json"))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["key"] for item in payload] == ["third", "legacy", "first"]


def test_categorized_dialogue_export_keeps_collection_order_for_duplicate_source_order(tmp_path: Path) -> None:
    collection = TranslationEntryCollection((
        _dialogue_entry("first-seen", 1),
        _dialogue_entry("duplicate", 1),
        _dialogue_entry("zero", 0),
    ))

    export_to_categorized_json_files(collection, tmp_path)

    output = next(tmp_path.glob("*.json"))
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["key"] for item in payload] == ["first-seen", "duplicate", "zero"]
