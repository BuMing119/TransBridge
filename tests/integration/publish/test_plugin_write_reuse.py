"""One publication reuses source parsing, never validation or mutable retry state."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
from pathlib import Path
from types import SimpleNamespace
import weakref

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import _run
from tests.integration.publish import test_bsa_localized_write as bsa_cases, test_bsa_shared_variant_ids as shared_cases
from tests.integration.publish.test_bsa_localized_write import _preflight, _strings
from tests.integration.publish.test_bsa_shared_variant_ids import (
    _TRANSLATIONS,
    _publish,
)
from tests.parser.test_original_match_plugin import quest_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.plugin_publication_source import plugin_source_scope, prepared_plugin_source
from transbridge.application.io.publish import FormatRoundTripValidator, OsPublishFilesystem
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup

bsa_source = bsa_cases.bsa_source
shared_source = shared_cases.shared_source


def _source_parse_counter(monkeypatch):
    original = PluginParser.parse_plugin
    calls = []

    def counted(parser, path, *args, **kwargs):
        calls.append(str(path))
        return original(parser, path, *args, **kwargs)

    monkeypatch.setattr(PluginParser, "parse_plugin", counted)
    return calls


def test_pure_localized_publication_preserves_exact_plugin_bytes_without_serialization(bsa_source, monkeypatch):
    def forbidden_save(*args):
        raise AssertionError("unchanged localized plugin must not be serialized")

    monkeypatch.setattr(SSEPluginWithContext, "save", forbidden_save)

    result = _run(_preflight(bsa_source))

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert bsa_source.target.read_bytes() == bsa_source.original_plugin
    assert PluginStringsLookup.from_plugin(bsa_source.target).get(1) == "Translated 1"
    assert bsa_source.source.read_bytes() == bsa_source.original_plugin
    assert bsa_source.archive.read_bytes() == bsa_source.original_archive


def test_bundle_parses_the_source_once_but_still_reparses_staged_plugin(bsa_source, monkeypatch):
    calls = _source_parse_counter(monkeypatch)
    real_from_file = SSEPluginWithContext.from_file
    staged_reads = []

    def read_plugin(path):
        if ".transbridge-publish-" in str(path):
            staged_reads.append(path)
        return real_from_file(path)

    monkeypatch.setattr(SSEPluginWithContext, "from_file", read_plugin)

    result = _run(_preflight(bsa_source))

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(calls) == 1
    assert len(staged_reads) == 1
    assert bsa_source.target.read_bytes() == bsa_source.original_plugin


def test_decoded_source_is_released_before_full_output_reparse(bsa_source, monkeypatch):
    original = SSEPluginWithContext.from_file
    source_refs = []
    verified = []

    def read_plugin(path):
        if ".transbridge-publish-" in str(path):
            assert source_refs and all(reference() is None for reference in source_refs)
            verified.append(path)
        plugin = original(path)
        if "transbridge-plugin-snapshot-" in str(path):
            source_refs.append(weakref.ref(plugin))
        return plugin

    monkeypatch.setattr(SSEPluginWithContext, "from_file", read_plugin)

    result = _run(_preflight(bsa_source))

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(verified) == 1
    assert bsa_source.target.read_bytes() == bsa_source.original_plugin


def test_cross_table_hydrated_type_recovery_reuses_the_same_source(shared_source, monkeypatch):
    calls = _source_parse_counter(monkeypatch)

    result = _publish(shared_source, _TRANSLATIONS)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(calls) == 1
    assert shared_source.target.read_bytes() == shared_source.original_plugin
    lookup = PluginStringsLookup.from_plugin(shared_source.target)
    assert lookup.get_for_type(148, "FULL") == "FULL-TEXT"
    assert lookup.get_for_type(148, "DESC") == "DESC-TEXT"
    assert lookup.get_for_type(148, "NAM1") == "INFO-TEXT"


def test_failed_publication_drops_prepared_source_before_retry(bsa_source, monkeypatch):
    class CorruptStage(OsPublishFilesystem):
        def read_bytes(self, path):
            data = super().read_bytes(path)
            return b"BAD!" + data[4:] if Path(path).suffix == ".esp" else data

    calls = _source_parse_counter(monkeypatch)
    checked = _preflight(bsa_source)

    failed = _run(checked, CorruptStage())
    completed = _run(checked)

    assert failed.outcome is OperationOutcome.FAILED
    assert completed.outcome is OperationOutcome.COMPLETED, completed.diagnostics
    assert len(calls) == 2
    assert len(set(calls)) == 2
    assert bsa_source.target.read_bytes() == bsa_source.original_plugin


def test_each_publication_owns_its_source_even_for_the_same_snapshot(bsa_source, monkeypatch):
    calls = _source_parse_counter(monkeypatch)
    second = SimpleNamespace(**{**vars(bsa_source), "target": bsa_source.output / "second.esp"})

    first_result = _run(_preflight(bsa_source))
    second_result = _run(_preflight(second))

    assert first_result.outcome is second_result.outcome is OperationOutcome.COMPLETED
    assert len(calls) == 2
    assert bsa_source.target.read_bytes() == second.target.read_bytes() == bsa_source.original_plugin


def test_inline_rerender_in_one_scope_uses_a_fresh_mutable_library_instance(tmp_path, monkeypatch):
    source = quest_plugin(tmp_path / "source.esp", ["Original", "Untouched"])
    strings = tmp_path / "Strings"
    strings.mkdir()
    payload = _strings({148: b"Auxiliary original"}, "strings")
    (strings / "source_English.strings").write_bytes(payload)
    adapter = SsePluginAdapter()
    context = RequestContext("inline-retry", run_id="inline-retry")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    request = WriteRequest(
        SourceDescriptor(str(tmp_path / "first.esp")),
        FormatId.PLUGIN_SSE,
        (replace(parsed.entries[0], translation="Changed", stage=1),),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
        options=(("source_authority", "hydration-v2"),),
    )
    calls = _source_parse_counter(monkeypatch)
    second_request = replace(request, target=SourceDescriptor(str(tmp_path / "second.esp")))

    with plugin_source_scope():
        first = adapter.write(request)
        second = adapter.write(second_request)
        report = FormatRoundTripValidator(adapter, OsPublishFilesystem()).validate(
            second_request, second_request.target.uri
        )

    assert first.outcome is second.outcome is OperationOutcome.COMPLETED
    assert report.valid, report.message
    assert len(calls) == 2
    for target in (tmp_path / "first.esp", tmp_path / "second.esp"):
        reparsed = adapter.parse(ParseRequest(SourceDescriptor(str(target)), context, FormatId.PLUGIN_SSE))
        assert [entry.original for entry in reparsed.entries] == ["Changed", "Untouched"]
    assert (strings / "second_English.strings").read_bytes() == payload


def test_nested_publications_restore_outer_source_and_release_parsers(bsa_source, monkeypatch):
    calls = _source_parse_counter(monkeypatch)
    snapshot = bsa_source.parsed.source_snapshot
    with plugin_source_scope():
        outer = prepared_plugin_source(snapshot, {})
        with plugin_source_scope():
            inner = prepared_plugin_source(snapshot, {})
            assert inner.parser is not outer.parser
        assert prepared_plugin_source(snapshot, {}) is outer
    parser_refs = (weakref.ref(outer.parser), weakref.ref(inner.parser))
    del outer, inner
    gc.collect()

    assert len(calls) == 2
    assert all(reference() is None for reference in parser_refs)


def test_concurrent_publications_do_not_share_mutable_plugin_objects(bsa_source, monkeypatch):
    calls = _source_parse_counter(monkeypatch)
    snapshot = bsa_source.parsed.source_snapshot

    def prepare():
        with plugin_source_scope():
            first = prepared_plugin_source(snapshot, {})
            assert prepared_plugin_source(snapshot, {}) is first
            return first.parser.get_plugin()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(lambda _: prepare(), range(2)))

    assert first is not second
    assert len(calls) == 2


def test_same_plugin_hash_with_different_captured_strings_is_not_a_cache_hit(bsa_source, monkeypatch):
    directory = bsa_source.output / "other-source"
    directory.mkdir()
    source = directory / bsa_source.source.name
    source.write_bytes(bsa_source.original_plugin)
    write_bsa(
        directory / bsa_source.archive.name,
        {name: content.replace(b"Original name", b"Changed! name") for name, content in bsa_source.members.items()},
    )
    parsed = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("other-source"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.source_snapshot.sha256 == bsa_source.parsed.source_snapshot.sha256
    calls = _source_parse_counter(monkeypatch)

    with plugin_source_scope():
        original = prepared_plugin_source(bsa_source.parsed.source_snapshot, {})
        other = prepared_plugin_source(parsed.source_snapshot, {})

    assert len(calls) == 2
    assert next(entry.original for entry in original.entries if entry.string_id == 1) == "Original name"
    assert next(entry.original for entry in other.entries if entry.string_id == 1) == "Changed! name"
