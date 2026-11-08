"""Content dispatch and conservative EET translation migration."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.eet_fixtures import eet_record, write_eet
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, SourceDescriptor, WriteRequest
from transbridge.application.io.eet_adapter import EetAdapter
from transbridge.application.io.eet_source import parse_eet_source, probe_eet_format
from transbridge.application.io.migration_import import MigrationImportError, prepare_migration_import
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files
from transbridge.parser.eet_binary_codec import EetDocument


@pytest.mark.parametrize("suffix", [".eet", ".xml"])
@pytest.mark.parametrize("binary", [False, True])
def test_eet_dispatch_follows_content_not_suffix(tmp_path, suffix, binary):
    path = tmp_path / ("source" + suffix)
    if binary:
        write_eet(path)
    else:
        path.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    expected = FormatId.BINARY_EET if binary else FormatId.XML_EET
    assert probe_eet_format(path) is expected
    result = parse_eet_source(path)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert result.format_id is expected
    assert result.source_snapshot.content == path.read_bytes()


@pytest.mark.parametrize("payload", [b"SSU8" + b"\0" * 5, b"<SSTXMLRessources/>", b"invalid"])
def test_eet_dispatch_does_not_accept_other_formats(tmp_path, payload):
    path = tmp_path / "wrong.eet"
    path.write_bytes(payload)
    assert parse_eet_source(path).outcome is OperationOutcome.FAILED
    with pytest.raises(ValueError):
        probe_eet_format(path)


def test_cancel_before_reading_missing_source(tmp_path):
    result = parse_eet_source(tmp_path / "absent.eet", cancellation=SimpleNamespace(is_cancelled=True))
    assert result.outcome is OperationOutcome.CANCELLED


@pytest.mark.parametrize("binary", [False, True])
def test_eet_parse_uses_one_captured_read_for_entries_and_snapshot(tmp_path, monkeypatch, binary):
    path = tmp_path / "captured.eet"
    if binary:
        write_eet(path)
    else:
        path.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    original = Path.read_bytes
    reads = []

    def read_once(current):
        if current == path:
            assert not reads, "The mutable source must not be reopened after capture"
            reads.append(current)
        return original(current)

    monkeypatch.setattr(Path, "read_bytes", read_once)
    parsed = parse_eet_source(path, cancellation=SimpleNamespace(is_cancelled=lambda: False))
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert len(reads) == 1


def _target(**changes):
    entry = TranslationEntry("Npc:00000800|1~NPC_FULL", "Greeting", "Hello", "", 0, "NPC_:FULL")
    return replace(entry, **changes)


def test_migration_uses_target_identity_and_preserves_target_metadata(tmp_path):
    path = write_eet(tmp_path / "source.eet")
    entry = _target()
    target = TranslationEntryCollection([entry])
    draft = prepare_migration_import(path, target)
    assert draft.format_id is FormatId.BINARY_EET
    assert draft.state_mapping() == {entry.identity: ("你好", 1)}
    assert tuple(target) == (entry,)


@pytest.mark.parametrize(
    "reason", ["source_duplicate", "target_duplicate", "form_id", "field", "original", "editor_id"]
)
def test_migration_skips_unproved_or_ambiguous_eet_records(tmp_path, reason):
    changes = {"form_id": {"form_id": "00000900"}, "field": {"field": "DESC"}, "original": {"original": "Other"}}
    records = [eet_record(**changes.get(reason, {}))]
    if reason == "source_duplicate":
        records.append(eet_record(index=2))
    path = write_eet(tmp_path / "source.eet", records)
    entries = [_target(editor_id="DifferentNpc" if reason == "editor_id" else "Greeting")]
    if reason == "target_duplicate":
        entries.append(_target(id="Npc:00000800|2~NPC_FULL", key="Second", entry_key=None))
    draft = prepare_migration_import(path, TranslationEntryCollection(entries))
    assert not draft.states and draft.skipped_conflicts


def test_empty_eet_translation_cannot_clear_a_target(tmp_path):
    path = write_eet(tmp_path / "empty.eet", [eet_record(translation="")])
    with pytest.raises(MigrationImportError, match="NO_TRANSLATIONS"):
        prepare_migration_import(path, TranslationEntryCollection([_target(translation="Keep", stage=1)]))


def test_eet_to_eet_uses_physical_record_fields_instead_of_shared_ordinals(tmp_path):
    original = write_eet(tmp_path / "base.eet", [eet_record(translation="")])
    parsed = parse_eet_source(original)
    translated = write_eet(tmp_path / "translated.eet", [eet_record(translation="Imported")])
    draft = prepare_migration_import(translated, TranslationEntryCollection(parsed.entries))
    assert draft.state_mapping() == {parsed.entries[0].identity: ("Imported", 1)}


def test_eet_paratranz_edit_and_native_write_preserve_duplicate_records(tmp_path):
    source = write_eet(tmp_path / "base.eet", [eet_record(translation=""), eet_record(index=2, translation="")])
    parsed = parse_eet_source(source)
    collection = TranslationEntryCollection(parsed.entries)
    output = tmp_path / "json"
    export_to_categorized_json_files(collection, output)
    exported = tuple(output.glob("*.json"))
    assert len(exported) == 1
    rows = json.loads(exported[0].read_text(encoding="utf-8"))
    assert len(rows) == 2 and len({row["key"] for row in rows}) == 2
    for i, row in enumerate(rows):
        row["translation"] = f"译文{i + 1}"
        row["stage"] = 1
    exported[0].write_text(json.dumps(rows), encoding="utf-8")
    states = prepare_migration_import(exported[0], collection, format_hint=FormatId.JSON_PARATRANZ).state_mapping()
    assert len(states) == 2
    entries = tuple(replace(entry, translation=states[entry.identity][0], stage=1) for entry in parsed.entries)
    target = tmp_path / "result.eet"
    result = EetAdapter().write(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.BINARY_EET,
            entries,
            1,
            RequestContext("json-roundtrip"),
            source_snapshot=parsed.source_snapshot,
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    actual = EetDocument.from_bytes(target.read_bytes())
    expected = EetDocument.from_bytes(source.read_bytes())
    for i, entry in enumerate(entries):
        expected = expected.with_translation(i, entry.translation)
    assert actual == expected
