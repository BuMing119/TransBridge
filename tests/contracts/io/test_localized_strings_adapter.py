from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil
import struct

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    FormatId,
    LocalizedStringsAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    WriteRequest,
)
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.pipeline import FomodPipeline

FIXTURES = Path(__file__).with_name("fixtures") / "strings"
ESP_FIXTURE = Path("tests/parser/data/sample.esp")


def _parse_request(path: Path, format_id: FormatId) -> ParseRequest:
    return ParseRequest(
        SourceDescriptor(str(path), path.name, path.stat().st_size),
        RequestContext("localized-contract", run_id="run-s05"),
        format_id,
    )


def _write_request(path: Path, format_id: FormatId, parsed, entries) -> WriteRequest:
    return WriteRequest(
        SourceDescriptor(str(path), path.name),
        format_id,
        tuple(entries),
        1,
        RequestContext("localized-contract", run_id="run-s05"),
        source_snapshot=parsed.source_snapshot,
    )


def _localized_plugin_fixture(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    plugin = tmp_path / "localized.esp"
    shutil.copyfile(ESP_FIXTURE, plugin)
    strings_dir = tmp_path / "Strings"
    strings_dir.mkdir()
    originals: dict[str, bytes] = {}
    for suffix in (".strings", ".dlstrings", ".ilstrings"):
        payload = (FIXTURES / f"integrity{suffix}").read_bytes()
        target = strings_dir / f"localized_English{suffix}"
        target.write_bytes(payload)
        originals[suffix] = payload
    return plugin, originals


@pytest.mark.parametrize(
    ("fixture", "format_id"),
    [
        ("integrity.strings", FormatId.STRINGS),
        ("integrity.dlstrings", FormatId.DLSTRINGS),
        ("integrity.ilstrings", FormatId.ILSTRINGS),
    ],
)
def test_real_fixture_untouched_round_trip_is_byte_identical(
    tmp_path: Path,
    fixture: str,
    format_id: FormatId,
) -> None:
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    adapter = LocalizedStringsAdapter(format_id)
    parsed = adapter.parse(_parse_request(source, format_id))
    target = tmp_path / f"out-{fixture}"

    written = adapter.write(_write_request(target, format_id, parsed, ()))
    reparsed = adapter.parse(_parse_request(target, format_id))

    assert parsed.outcome is OperationOutcome.COMPLETED
    assert written.outcome is OperationOutcome.COMPLETED
    assert target.read_bytes() == source.read_bytes()
    assert [entry.string_id for entry in reparsed.entries] == [entry.string_id for entry in parsed.entries]
    assert [entry.original for entry in reparsed.entries] == [entry.original for entry in parsed.entries]


@pytest.mark.parametrize(
    ("fixture", "format_id"),
    [
        ("integrity.strings", FormatId.STRINGS),
        ("integrity.dlstrings", FormatId.DLSTRINGS),
        ("integrity.ilstrings", FormatId.ILSTRINGS),
    ],
)
def test_real_fixture_modify_rebuild_preserves_id_order_encoding_and_unmodified_bytes(
    tmp_path: Path,
    fixture: str,
    format_id: FormatId,
) -> None:
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    adapter = LocalizedStringsAdapter(format_id)
    parsed = adapter.parse(_parse_request(source, format_id))
    original_records = dict(parsed.source_snapshot.metadata)["strings.records"]
    changed = replace(parsed.entries[0], translation="Translated ✓", stage=1)
    hidden = replace(parsed.entries[1], translation="must not publish", stage=-1)
    target = tmp_path / f"out-{fixture}"

    written = adapter.write(_write_request(target, format_id, parsed, (changed, hidden)))
    reparsed = adapter.parse(_parse_request(target, format_id))
    rebuilt_records = dict(reparsed.source_snapshot.metadata)["strings.records"]

    assert written.outcome is OperationOutcome.COMPLETED
    assert written.diagnostics[0].code == "STAGE_MAPPING_LOSSY"
    assert [record.string_id for record in rebuilt_records] == [record.string_id for record in original_records]
    assert reparsed.entries[0].original == "Translated ✓"
    assert reparsed.entries[1].original == parsed.entries[1].original
    assert rebuilt_records[1].encoding == original_records[1].encoding == "utf-8-sig"
    assert rebuilt_records[1].raw_payload == original_records[1].raw_payload
    assert rebuilt_records[2].encoding == original_records[2].encoding == "cp1252"
    assert rebuilt_records[2].raw_payload == original_records[2].raw_payload


@pytest.mark.parametrize(
    ("fixture", "format_id"),
    [
        ("integrity.strings", FormatId.STRINGS),
        ("integrity.dlstrings", FormatId.DLSTRINGS),
        ("integrity.ilstrings", FormatId.ILSTRINGS),
    ],
)
def test_locked_empty_blocks_formal_write_without_artifact(
    tmp_path: Path,
    fixture: str,
    format_id: FormatId,
) -> None:
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    adapter = LocalizedStringsAdapter(format_id)
    parsed = adapter.parse(_parse_request(source, format_id))
    locked = replace(parsed.entries[0], translation="", stage=9)
    target = tmp_path / f"blocked-{fixture}"

    result = adapter.write(_write_request(target, format_id, parsed, (locked,)))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "STAGE_LOCKED_TRANSLATION_REQUIRED"
    assert dict(result.diagnostics[0].details)["string_id"] == locked.string_id
    assert not target.exists()


def test_duplicate_string_id_is_failed_with_record_indices(tmp_path: Path) -> None:
    chunks = [b"first\x00", b"second\x00"]
    data_size = sum(map(len, chunks))
    duplicate = (
        struct.pack("<II", 2, data_size)
        + struct.pack("<II", 7, 0)
        + struct.pack("<II", 7, len(chunks[0]))
        + b"".join(chunks)
    )
    source = tmp_path / "duplicate.strings"
    source.write_bytes(duplicate)

    result = LocalizedStringsAdapter(FormatId.STRINGS).parse(_parse_request(source, FormatId.STRINGS))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "LOCALIZED_STRING_ID_DUPLICATE"
    assert dict(result.diagnostics[0].details) == {"string_id": 7, "record_indices": (0, 1)}


def test_invalid_stage_and_cp1252_encoding_loss_block_before_artifact(tmp_path: Path) -> None:
    source = tmp_path / "integrity.strings"
    shutil.copyfile(FIXTURES / "integrity.strings", source)
    adapter = LocalizedStringsAdapter(FormatId.STRINGS)
    parsed = adapter.parse(_parse_request(source, FormatId.STRINGS))
    invalid = replace(parsed.entries[0], translation="translated", stage=4)
    cp1252_unrepresentable = replace(parsed.entries[2], translation="中文", stage=1)

    invalid_target = tmp_path / "invalid.strings"
    invalid_result = adapter.write(_write_request(invalid_target, FormatId.STRINGS, parsed, (invalid,)))
    encoding_target = tmp_path / "encoding.strings"
    encoding_result = adapter.write(
        _write_request(encoding_target, FormatId.STRINGS, parsed, (cp1252_unrepresentable,))
    )

    assert invalid_result.outcome is OperationOutcome.FAILED
    assert invalid_result.diagnostics[0].code == "STAGE_INVALID"
    assert not invalid_target.exists()
    assert encoding_result.outcome is OperationOutcome.FAILED
    assert encoding_result.diagnostics[0].code == "LOCALIZED_STRINGS_WRITE_FAILED"
    assert not encoding_target.exists()


@pytest.mark.parametrize(
    ("fixture", "format_id"),
    [
        ("integrity.strings", FormatId.STRINGS),
        ("integrity.dlstrings", FormatId.DLSTRINGS),
        ("integrity.ilstrings", FormatId.ILSTRINGS),
    ],
)
@pytest.mark.parametrize("source_state", ["removed", "changed"])
@pytest.mark.parametrize("hydrated", [False, True])
def test_only_hydrated_writes_are_independent_of_the_original_source(
    tmp_path: Path, fixture, format_id, source_state, hydrated
) -> None:
    source = tmp_path / fixture
    shutil.copyfile(FIXTURES / fixture, source)
    adapter = LocalizedStringsAdapter(format_id)
    parsed = adapter.parse(_parse_request(source, format_id))
    changed = replace(parsed.entries[0], translation="Translated from snapshot", stage=1)
    target = tmp_path / f"out-{fixture}"
    request = _write_request(target, format_id, parsed, (changed,))
    if hydrated:
        request = replace(request, options=(("source_authority", "hydration-v2"),))
    if source_state == "removed":
        source.unlink()
    else:
        source.write_bytes(b"new source revision")

    result = adapter.write(request)

    if not hydrated:
        assert result.outcome is OperationOutcome.FAILED
        expected = "SOURCE_SNAPSHOT_UNAVAILABLE" if source_state == "removed" else "SOURCE_FINGERPRINT_CONFLICT"
        assert result.diagnostics[0].code == expected
        assert not target.exists()
        return
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    reparsed = adapter.parse(_parse_request(target, format_id))
    assert [entry.string_id for entry in reparsed.entries] == [entry.string_id for entry in parsed.entries]
    assert reparsed.entries[0].original == changed.translation
    assert [entry.original for entry in reparsed.entries[1:]] == [entry.original for entry in parsed.entries[1:]]


def test_hydrated_snapshot_rejects_same_size_content_corruption(tmp_path: Path) -> None:
    source = tmp_path / "integrity.strings"
    shutil.copyfile(FIXTURES / source.name, source)
    adapter = LocalizedStringsAdapter(FormatId.STRINGS)
    parsed = adapter.parse(_parse_request(source, FormatId.STRINGS))
    with pytest.raises(ValueError, match="source snapshot hash does not match content"):
        replace(parsed.source_snapshot, content=b"X" + parsed.source_snapshot.content[1:])


def test_sse_snapshot_captures_and_rebuilds_all_loose_localized_variants(tmp_path: Path) -> None:
    plugin, originals = _localized_plugin_fixture(tmp_path)
    adapter = SsePluginAdapter()
    parsed = adapter.parse(_parse_request(plugin, FormatId.PLUGIN_SSE))
    localized_sources = dict(parsed.source_snapshot.metadata)["localized_sources"]
    changed = replace(parsed.entries[0], translation="Localized adapter chain", stage=1)

    written = adapter.write(_write_request(plugin, FormatId.PLUGIN_SSE, parsed, (changed,)))
    reparsed = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(plugin), plugin.name, plugin.stat().st_size),
            RequestContext("localized-contract", run_id="run-s05-reparse"),
            FormatId.PLUGIN_SSE,
            source_namespace=changed.identity.namespace,
        )
    )

    assert parsed.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}
    assert len(localized_sources) == 3
    assert written.outcome is OperationOutcome.COMPLETED
    matching = [entry for entry in reparsed.entries if entry.identity == changed.identity]
    assert len(matching) == 1 and matching[0].original == "Localized adapter chain"
    for suffix, payload in originals.items():
        assert (tmp_path / "Strings" / f"localized_English{suffix}").read_bytes() == payload


def test_fomod_write_back_uses_same_snapshot_rebuild_and_locked_gate(tmp_path: Path) -> None:
    plugin, originals = _localized_plugin_fixture(tmp_path)
    adapter = SsePluginAdapter()
    parsed = adapter.parse(_parse_request(plugin, FormatId.PLUGIN_SSE))
    changed = replace(parsed.entries[0], translation="FOMOD unified I/O", stage=1)
    translated_collection = TranslationEntryCollection((changed, *parsed.entries[1:]))

    FomodPipeline()._write_back(plugin, translated_collection)

    reparsed = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(plugin), plugin.name, plugin.stat().st_size),
            RequestContext("localized-contract", run_id="run-s05-fomod-reparse"),
            FormatId.PLUGIN_SSE,
            source_namespace=changed.identity.namespace,
        )
    )
    matching = [entry for entry in reparsed.entries if entry.identity == changed.identity]
    assert len(matching) == 1 and matching[0].original == "FOMOD unified I/O"
    for suffix, payload in originals.items():
        assert (tmp_path / "Strings" / f"localized_English{suffix}").read_bytes() == payload

    locked = replace(reparsed.entries[0], translation="", stage=9)
    locked_collection = TranslationEntryCollection((locked, *reparsed.entries[1:]))
    plugin_before = plugin.read_bytes()
    with pytest.raises(RuntimeError, match="STAGE_LOCKED_TRANSLATION_REQUIRED"):
        FomodPipeline()._write_back(plugin, locked_collection)
    assert plugin.read_bytes() == plugin_before
    for suffix, payload in originals.items():
        assert (tmp_path / "Strings" / f"localized_English{suffix}").read_bytes() == payload
