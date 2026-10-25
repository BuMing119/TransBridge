"""Localized capture must preserve empty sources and ignore irrelevant resources."""

from dataclasses import replace
import struct
from types import SimpleNamespace

import pytest

from tests.bsa_fixtures import write_bsa
from tests.integration.publish.test_bsa_localized_write import _field, _group, _record, _strings
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.plugin_localized_sources import _localized_source_snapshots
from transbridge.parser.plugin_parser import PluginParser


def _plugin(tmp_path, *, localized):
    path = tmp_path / "mod.esp"
    text = struct.pack("<I", 1) if localized else b"Inline text\0"
    path.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, 1, 0x801)), 0x80 if localized else 0)
        + _group("WEAP", _record("WEAP", 0x800, _field("EDID", b"Weapon\0") + _field("FULL", text)))
    )
    return path


def _request(path):
    return ParseRequest(SourceDescriptor(str(path)), RequestContext("capture-boundaries"), FormatId.PLUGIN_SSE)


def test_nonlocalized_plugin_ignores_corrupt_unrelated_archive(tmp_path):
    path = _plugin(tmp_path, localized=False)
    (tmp_path / "mod.bsa").write_bytes(b"unrelated broken archive")

    parsed = SsePluginAdapter().parse(_request(path))
    legacy = PluginParser()
    entries = legacy.parse_plugin(path)

    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert [entry.original for entry in parsed.entries] == ["Inline text"]
    assert [entry.original for entry in entries] == ["Inline text"]
    assert dict(parsed.source_snapshot.metadata)["localized_sources"] == ()
    assert legacy.get_strings_lookup() is None


def test_inline_valid_loose_table_is_preserved_without_entering_unrelated_archive(tmp_path):
    path = _plugin(tmp_path, localized=False)
    archive = tmp_path / "mod.bsa"
    archive.write_bytes(b"unrelated broken archive")
    strings = tmp_path / "Strings"
    strings.mkdir()
    payload = _strings({148: b"Caf\xe9 unrelated but preserved"}, "strings")
    (strings / "mod_English.strings").write_bytes(payload)
    adapter = SsePluginAdapter()
    parsed = adapter.parse(_request(path))
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert len(dict(parsed.source_snapshot.metadata)["localized_sources"]) == 1
    target = tmp_path / "output" / "translated.esp"

    result = adapter.write(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            (replace(parsed.entries[0], translation="Changed inline", stage=1),),
            1,
            RequestContext("inline-write"),
            source_snapshot=parsed.source_snapshot,
        )
    )

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert (target.parent / "Strings" / "translated_English.strings").read_bytes() == payload
    assert archive.read_bytes() == b"unrelated broken archive"
    assert [entry.original for entry in adapter.parse(_request(target)).entries] == ["Changed inline"]


def test_localized_partial_loose_table_still_rejects_corrupt_archive_dependency(tmp_path):
    path = _plugin(tmp_path, localized=True)
    (tmp_path / "mod.bsa").write_bytes(b"broken dependency")
    strings = tmp_path / "Strings"
    strings.mkdir()
    (strings / "mod_English.strings").write_bytes(_strings({1: b"Name"}, "strings"))

    parsed = SsePluginAdapter().parse(_request(path))

    assert parsed.outcome is OperationOutcome.FAILED
    assert parsed.source_snapshot is None
    assert any(item.code == "PLUGIN_PARSE_FAILED" for item in parsed.diagnostics)


@pytest.mark.parametrize("storage", ["archive", "loose"])
def test_empty_localized_tables_are_captured_even_without_a_lookup(tmp_path, storage):
    path = _plugin(tmp_path, localized=True)
    content = {f"mod_english.{suffix}": _strings({}, suffix) for suffix in ("strings", "dlstrings", "ilstrings")}
    if storage == "archive":
        write_bsa(tmp_path / "mod.bsa", {f"strings/{name}": raw for name, raw in content.items()})
    else:
        strings = tmp_path / "Strings"
        strings.mkdir()
        for name, raw in content.items():
            (strings / name).write_bytes(raw)

    parsed = SsePluginAdapter().parse(_request(path))

    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    sources = dict(parsed.source_snapshot.metadata)["localized_sources"]
    assert len(sources) == 3
    assert all(snapshot.content == struct.pack("<II", 0, 0) and entries == () for snapshot, entries in sources)


def test_cancellation_during_localized_capture_is_reported_as_cancelled(tmp_path, monkeypatch):
    path = _plugin(tmp_path, localized=True)
    strings = tmp_path / "Strings"
    strings.mkdir()
    (strings / "mod_English.strings").write_bytes(_strings({1: b"Name"}, "strings"))
    request = _request(path)

    def cancelled_capture(*args, **kwargs):
        return _localized_source_snapshots(
            path, "english", replace(request, cancellation=SimpleNamespace(is_cancelled=True))
        )

    monkeypatch.setattr("transbridge.application.io.legacy_adapters._localized_source_snapshots", cancelled_capture)

    parsed = SsePluginAdapter().parse(request)

    assert parsed.outcome is OperationOutcome.CANCELLED
    assert parsed.source_snapshot is None
    assert not any(item.code == "PLUGIN_LOCALIZED_SNAPSHOT_FAILED" for item in parsed.diagnostics)
