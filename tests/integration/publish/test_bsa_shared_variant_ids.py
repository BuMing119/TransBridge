"""Equal numeric IDs in different localized tables remain independent resources."""

from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import _run
from tests.integration.publish.test_bsa_localized_write import _field, _group, _record, _strings
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.publish import FormatRoundTripValidator, OsPublishFilesystem
from transbridge.parser.strings_file import SkyrimStringsFile

_ORIGINALS = {
    "WEAP FULL": "FULL original",
    "NPC_ FULL": "FULL original",
    "WEAP DESC": "DESC original",
    "INFO NAM1": "INFO original",
}
_TRANSLATIONS = {"WEAP FULL": "FULL-TEXT", "NPC_ FULL": "FULL-TEXT", "WEAP DESC": "DESC-TEXT", "INFO NAM1": "INFO-TEXT"}


@pytest.fixture(params=["mod.esp", "Dragonborn.esm"])
def shared_source(tmp_path, request):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = source_dir / request.param
    identifier = struct.pack("<I", 148)
    weapon = _record(
        "WEAP", 0x800, _field("EDID", b"Weapon\0") + _field("FULL", identifier) + _field("DESC", identifier)
    )
    npc = _record("NPC_", 0x801, _field("EDID", b"SharedName\0") + _field("FULL", identifier))
    dialogue = _record("INFO", 0x802, _field("EDID", b"Dialogue\0") + _field("NAM1", identifier))
    source.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, 3, 0x803)), 0x80)
        + _group("WEAP", weapon)
        + _group("NPC_", npc)
        + _group("INFO", dialogue)
    )
    tables = {
        "strings": _strings({148: b"FULL original", 149: b"Caf\xe9 preserved"}, "strings"),
        "dlstrings": _strings({148: b"DESC original", 149: b"Unreferenced description"}, "dlstrings"),
        "ilstrings": _strings({148: b"INFO original", 149: b"Unreferenced dialogue"}, "ilstrings"),
    }
    archive_name = "Skyrim - Interface.BSA" if source.stem == "Dragonborn" else "mod.BSA"
    archive = write_bsa(
        source_dir / archive_name,
        {f"strings/{source.stem.lower()}_english.{suffix}": raw for suffix, raw in tables.items()},
    )
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("shared-id"), FormatId.PLUGIN_SSE)
    )
    output = tmp_path / "output"
    output.mkdir()
    return SimpleNamespace(
        source=source,
        archive=archive,
        parsed=parsed,
        target=output / "translated.esp",
        output=output,
        tables=tables,
        original_plugin=source.read_bytes(),
        original_archive=archive.read_bytes(),
    )


def _parsed_entries(case):
    assert case.parsed.outcome is OperationOutcome.COMPLETED, case.parsed.diagnostics
    assert len(case.parsed.entries) == 4
    assert {entry.dsd_type: entry.original for entry in case.parsed.entries} == _ORIGINALS
    assert all(entry.string_id == 148 for entry in case.parsed.entries)
    return case.parsed.entries


def _request(case, translations):
    return WriteRequest(
        SourceDescriptor(str(case.target)),
        FormatId.PLUGIN_SSE,
        tuple(
            replace(entry, translation=translations[entry.dsd_type], stage=1)
            for entry in _parsed_entries(case)
            if entry.dsd_type in translations
        ),
        1,
        RequestContext("shared-id", run_id="shared-id"),
        source_snapshot=case.parsed.source_snapshot,
        options=(("source_authority", "hydration-v2"),),
    )


def _publish(case, translations):
    request = _request(case, translations)
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            request.source_snapshot,
            request.format_id,
            tuple(entry.snapshot() for entry in request.entries),
            request.target.uri,
            request.variant_revision,
            request.context,
        )
    )
    assert checked.ready
    return _run(checked)


def _assert_sources_unchanged(case):
    assert case.source.read_bytes() == case.original_plugin
    assert case.archive.read_bytes() == case.original_archive


def _output_originals(case):
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(case.target)), RequestContext("verify-shared-id"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert len(parsed.entries) == 4
    return {entry.dsd_type: entry.original for entry in parsed.entries}


def test_shared_id_across_three_real_bsa_tables_parses_and_publishes_independent_text(shared_source):
    result = _publish(shared_source, _TRANSLATIONS)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _output_originals(shared_source) == _TRANSLATIONS
    assert {Path(path) for path in result.artifact_refs} == {
        shared_source.target,
        *(shared_source.output / "Strings" / f"translated_English.{suffix}" for suffix in shared_source.tables),
    }
    for suffix, expected in (("strings", "FULL-TEXT"), ("dlstrings", "DESC-TEXT"), ("ilstrings", "INFO-TEXT")):
        output = shared_source.output / "Strings" / f"translated_English.{suffix}"
        strings = SkyrimStringsFile.from_file(output)
        assert strings.get(148) == expected
        assert strings.get(149) == SkyrimStringsFile.from_bytes(shared_source.tables[suffix], "." + suffix).get(149)
    _assert_sources_unchanged(shared_source)


@pytest.mark.parametrize(
    ("record_type", "suffix"), [("WEAP FULL", "strings"), ("WEAP DESC", "dlstrings"), ("INFO NAM1", "ilstrings")]
)
def test_changing_one_shared_id_variant_preserves_other_tables_byte_for_byte(shared_source, record_type, suffix):
    result = _publish(shared_source, {record_type: "Changed only this table"})

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    expected = dict(_ORIGINALS)
    expected[record_type] = "Changed only this table"
    if suffix == "strings":
        expected["NPC_ FULL"] = "Changed only this table"
    assert _output_originals(shared_source) == expected
    for variant, original in shared_source.tables.items():
        if variant != suffix:
            output = shared_source.output / "Strings" / f"translated_English.{variant}"
            assert output.read_bytes() == original
    _assert_sources_unchanged(shared_source)


@pytest.mark.parametrize(
    ("suffix", "expected", "polluted"),
    [
        ("strings", "FULL-TEXT", "DESC-TEXT"),
        ("dlstrings", "DESC-TEXT", "FULL-TEXT"),
        ("ilstrings", "INFO-TEXT", "FULL-TEXT"),
    ],
)
def test_fidelity_rejects_valid_binary_cross_variant_text_pollution(shared_source, suffix, expected, polluted):
    result = _publish(shared_source, _TRANSLATIONS)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    output = shared_source.output / "Strings" / f"translated_English.{suffix}"
    content = output.read_bytes()
    assert content.count(expected.encode()) == 1
    output.write_bytes(content.replace(expected.encode(), polluted.encode()))

    report = FormatRoundTripValidator(SsePluginAdapter(), OsPublishFilesystem()).validate(
        _request(shared_source, _TRANSLATIONS), str(shared_source.target)
    )

    assert not report.valid
    assert report.code == "FIDELITY_MISMATCH"
    _assert_sources_unchanged(shared_source)


def test_same_variant_shared_references_cannot_request_inconsistent_translations(shared_source):
    result = _publish(shared_source, _TRANSLATIONS)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    request = _request(shared_source, {**_TRANSLATIONS, "NPC_ FULL": "Inconsistent same-table text"})

    report = FormatRoundTripValidator(SsePluginAdapter(), OsPublishFilesystem()).validate(
        request, str(shared_source.target)
    )

    assert not report.valid
    assert report.code == "FIDELITY_SUMMARY_FAILED"
    before = {path: path.read_bytes() for path in shared_source.output.rglob("*") if path.is_file()}
    rejected_target = shared_source.output / "rejected.esp"
    rejected = SsePluginAdapter().write(replace(request, target=SourceDescriptor(str(rejected_target))))
    assert rejected.outcome is OperationOutcome.FAILED
    assert not rejected_target.exists()
    assert {path: path.read_bytes() for path in shared_source.output.rglob("*") if path.is_file()} == before
    _assert_sources_unchanged(shared_source)
