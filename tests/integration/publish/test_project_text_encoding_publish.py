"""Explicit project codecs survive real staged publication and translation import."""

from dataclasses import replace
from pathlib import Path
import struct

import pytest

from tests.bsa_fixtures import write_bsa
from tests.contracts.io.test_localized_hydrated_publication import _run
from tests.integration.publish.test_bsa_localized_write import bsa_source as bsa_fixture
from tests.integration.publish.test_plugin_fidelity import _publish
from tests.plugin_fixtures import _record
from tests.source_format_fixtures import write_translation_source
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    FormatId,
    LocalizedStringsAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    WriteRequest,
)
from transbridge.application.io.open_file_options import OpenFileOptions
from transbridge.application.io.open_translation_file import prepare_open_file
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.plugin_transfer import PluginTransferDraft, PluginTransferPreflightService
from transbridge.application.io.publish import ConflictPolicy, FormatRoundTripValidator, OsPublishFilesystem
from transbridge.application.io.use_case import TranslationIoUseCase
from transbridge.config.text_encoding import LEGACY_READ_POLICY, XTRANSLATOR_READ_POLICY
from transbridge.parser.localized_string_records import encode_chunk, parse_records, render
from transbridge.parser.plugin_parser import PluginParser

bsa_source = bsa_fixture


def _plugin(path, texts, encoding):
    def field(kind, text):
        payload = text.encode(encoding) + b"\0"
        return kind + struct.pack("<H", len(payload)) + payload

    records = b"".join(
        _record(b"NPC_", 0x800 + index, field(b"EDID", f"Entry{index}") + field(b"FULL", text))
        for index, text in enumerate(texts)
    )
    path.write_bytes(
        _record(b"TES4", 0, b"") + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), b"NPC_", 0, 0, 0, 0) + records
    )
    return path


def _parsed(source, encoding):
    result = SsePluginAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(source)),
            RequestContext("encoding-test", run_id="encoding-test"),
            FormatId.PLUGIN_SSE,
            options=(("input_encoding", encoding),),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    return result


@pytest.mark.parametrize(
    ("source_codec", "target_codec", "texts"),
    [
        ("cp1252", "utf-8", ("Café", "Résumé")),
        ("utf-8", "cp936", ("你好", "未翻译")),
    ],
)
def test_pure_transcode_publishes_all_unrequested_text_with_output_codec_validation(
    tmp_path, source_codec, target_codec, texts
):
    source = _plugin(tmp_path / "source.esp", texts, source_codec)
    parsed = _parsed(source, source_codec)
    original = source.read_bytes()
    target = tmp_path / "output.esp"
    result = _publish(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            (),
            1,
            RequestContext("encoding-test", run_id="encoding-test"),
            source_snapshot=parsed.source_snapshot,
            options=(("input_encoding", source_codec), ("output_encoding", target_codec)),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert result.validation.fidelity_valid
    assert [entry.original for entry in PluginParser().parse_plugin(target, input_encoding=target_codec)] == list(texts)
    assert all(text.encode(target_codec) + b"\0" in target.read_bytes() for text in texts)
    assert source.read_bytes() == original


@pytest.mark.parametrize(("policy", "expected"), [(XTRANSLATOR_READ_POLICY, "Annaïg"), (LEGACY_READ_POLICY, "Annaďg")])
def test_utf8_write_transcodes_using_the_bound_snapshot_policy(tmp_path, policy, expected):
    source = _plugin(tmp_path / "source.esp", ("Annaïg",), "cp1252")
    context = RequestContext("encoding-test", run_id="encoding-test")
    parsed = SsePluginAdapter().parse(
        ParseRequest(
            SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE, options=(("text_read_policy", policy),)
        )
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.entries[0].original == expected
    assert dict(parsed.source_snapshot.metadata)["text_read_policy"] == policy
    target = tmp_path / "output.esp"
    request = WriteRequest(
        SourceDescriptor(str(target)),
        FormatId.PLUGIN_SSE,
        (),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
        options=(("source_language", "russian"), ("input_encoding", "cp1251"), ("output_encoding", "utf-8")),
    )

    result = _publish(request)

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert expected.encode() + b"\0" in target.read_bytes()
    assert b"Anna\xefg\0" in source.read_bytes()


def test_publication_reparse_rejects_residual_fallback_bytes_in_utf8_plugin(tmp_path):
    source = _plugin(tmp_path / "source.esp", ("Annaïg",), "cp1252")
    parsed = _parsed(source, "utf-8")
    target = _plugin(tmp_path / "output.esp", ("Annaïg",), "cp1252")
    adapter = SsePluginAdapter()
    request = WriteRequest(
        SourceDescriptor(str(target)),
        FormatId.PLUGIN_SSE,
        (),
        1,
        RequestContext("encoding-test"),
        source_snapshot=parsed.source_snapshot,
    )

    report = FormatRoundTripValidator(adapter, OsPublishFilesystem()).validate(request, str(target))

    assert not report.valid
    assert report.code == "REPARSE_FAILED"
    assert "cannot decode as utf-8" in report.message


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        (((7, b"Caf\xe9"), (19, b"Other")), "REPARSE_FAILED"),
        (((7, b"Changed"), (19, b"Other")), "FIDELITY_MISMATCH"),
        (((19, b"Other"), (7, b"Caf\xc3\xa9")), "FIDELITY_MISMATCH"),
    ],
)
def test_strings_validator_checks_all_utf8_records_including_unrequested_ones(tmp_path, rows, code):
    context = RequestContext("encoding-test")
    adapter = LocalizedStringsAdapter(FormatId.STRINGS)
    source = tmp_path / "source.strings"
    target = tmp_path / "staged.strings"
    source.write_bytes(render([(7, b"Caf\xe9\0"), (19, b"Other\0")]))
    target.write_bytes(render([(identifier, payload + b"\0") for identifier, payload in rows]))
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.STRINGS))
    request = WriteRequest(
        SourceDescriptor(str(target)),
        FormatId.STRINGS,
        (),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
    )

    report = FormatRoundTripValidator(adapter, OsPublishFilesystem()).validate(request, str(target))

    assert not report.valid
    assert report.code == code


def test_unrepresentable_untranslated_text_aborts_before_publication(tmp_path):
    source = _plugin(tmp_path / "source.esp", ("你好",), "utf-8")
    parsed = _parsed(source, "utf-8")
    target = tmp_path / "output.esp"
    result = _publish(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            (),
            1,
            RequestContext("encoding-test", run_id="encoding-test"),
            source_snapshot=parsed.source_snapshot,
            options=(("input_encoding", "utf-8"), ("output_encoding", "cp1252")),
        )
    )
    assert result.outcome is OperationOutcome.FAILED
    assert not target.exists()
    assert "cp1252" in result.message
    assert "FULL" in result.message


def test_encoding_failure_preserves_existing_output_and_source(tmp_path):
    source = _plugin(tmp_path / "source.esp", ("你好",), "utf-8")
    parsed = _parsed(source, "utf-8")
    before = source.read_bytes()
    target = tmp_path / "output.esp"
    target.write_bytes(b"existing output")
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            parsed.source_snapshot,
            FormatId.PLUGIN_SSE,
            tuple(entry.snapshot() for entry in parsed.entries),
            str(target),
            1,
            RequestContext("encoding-test"),
            conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE,
            options=(("input_encoding", "utf-8"), ("output_encoding", "cp1252")),
        )
    )
    assert checked.ready, checked.checks
    result = _run(checked)
    assert result.outcome is OperationOutcome.FAILED
    assert target.read_bytes() == b"existing output"
    assert source.read_bytes() == before


def test_bsa_pure_transcode_includes_unreferenced_ids_and_each_variant(bsa_source):
    case = bsa_source
    members = dict(case.members)
    for suffix, content in case.strings.items():
        records = parse_records(content, suffix != "strings", "utf-8")
        members[f"strings/{case.source.stem.lower()}_english.{suffix}"] = render([
            (row.string_id, encode_chunk(row.text, "cp1252", suffix != "strings")) for row in records
        ])
    write_bsa(case.archive, members)
    before = case.archive.read_bytes()
    parsed = _parsed(case.source, "utf-8")
    assert dict(parsed.diagnostics[0].details) == {"count": 1}
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            parsed.source_snapshot,
            FormatId.PLUGIN_SSE,
            tuple(entry.snapshot() for entry in parsed.entries),
            str(case.target),
            1,
            RequestContext("encoding-test"),
            options=(("input_encoding", "cp1252"), ("output_encoding", "utf-8"), ("target_language", "russian")),
        )
    )
    assert checked.ready, checked.checks
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    for suffix, content in case.strings.items():
        output = case.output / "Strings" / f"translated_English.{suffix}"
        assert output.read_bytes() == content
    assert case.archive.read_bytes() == before
    assert case.target.read_bytes() == case.original_plugin


@pytest.mark.parametrize("format_id", [FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS])
def test_strings_transcodes_untouched_and_locked_source_text(tmp_path, format_id):
    prefixed = format_id is not FormatId.STRINGS
    source = tmp_path / ("source." + format_id.value.split(".")[-1])
    source.write_bytes(
        render([(7, encode_chunk("你好", "cp936", prefixed)), (9, encode_chunk("世界", "cp936", prefixed))])
    )
    opened = prepare_open_file(str(source), OpenFileOptions(), RequestContext("test"), input_encoding="cp936")
    assert [entry.original for entry in opened.source.hydration.entries] == ["你好", "世界"]
    adapter = LocalizedStringsAdapter(format_id)
    parsed = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(source)), RequestContext("test"), format_id, options=(("input_encoding", "cp936"),)
        )
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    target = tmp_path / ("output." + format_id.value.split(".")[-1])
    locked = replace(parsed.entries[0], translation="", stage=9)
    result = adapter.write(
        WriteRequest(
            SourceDescriptor(str(target)),
            format_id,
            (locked,),
            1,
            RequestContext("test"),
            source_snapshot=parsed.source_snapshot,
            options=(("output_encoding", "utf-8"),),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    reread = adapter.parse(ParseRequest(SourceDescriptor(str(target)), RequestContext("test"), format_id))
    assert [entry.original for entry in reread.entries] == ["你好", "世界"]
    assert source.read_bytes() != target.read_bytes()


def test_open_file_and_optional_plugin_donor_share_input_encoding(tmp_path):
    source = _plugin(tmp_path / "source.esp", ("Café",), "cp1252")
    donor = _plugin(tmp_path / "donor.esp", ("Résumé",), "cp1252")
    opened = prepare_open_file(
        str(source), OpenFileOptions(translation_path=str(donor)), RequestContext("test"), input_encoding="cp1252"
    )
    assert opened.source.hydration.entries[0].original == "Café"
    assert opened.source.hydration.entries[0].translation == "Résumé"


def test_transfer_reconstructs_source_and_template_using_captured_input_codec(tmp_path):
    source = _plugin(tmp_path / "source.esp", ("Café",), "cp1252")
    template = _plugin(tmp_path / "template.esp", ("Café",), "cp1252")
    parsed = _parsed(source, "cp1252")
    entry = replace(parsed.entries[0], translation="你好", stage=1)
    draft = HydratedWriteDraft(
        parsed.source_snapshot,
        FormatId.PLUGIN_SSE,
        (entry.snapshot(),),
        str(tmp_path / "output.esp"),
        1,
        RequestContext("test"),
        options=(("input_encoding", "cp1252"), ("output_encoding", "cp936")),
    )
    checked = PluginTransferPreflightService().preflight(PluginTransferDraft(draft, str(template)))
    assert checked.ready, checked.checks
    assert checked.report.writable_count == 1


@pytest.mark.parametrize(
    "format_id", [FormatId.BINARY_EET, FormatId.SST_SSU8, FormatId.SST_SSU9, FormatId.XML_EET, FormatId.XML_XT]
)
def test_native_translation_archive_ignores_plugin_codepages(tmp_path, format_id):
    if format_id in {FormatId.XML_EET, FormatId.XML_XT}:
        name = "eet-small.xml" if format_id is FormatId.XML_EET else "xt-small.xml"
        path = tmp_path / name
        path.write_bytes((Path("tests/contracts/io/fixtures") / name).read_bytes())
    else:
        path = write_translation_source(tmp_path, format_id.value)
    io = TranslationIoUseCase()
    parsed = io.parse(
        ParseRequest(
            SourceDescriptor(str(path)), RequestContext("test"), format_id, options=(("input_encoding", "cp1252"),)
        )
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    target = tmp_path / ("output" + path.suffix)
    entries = tuple(replace(entry, translation="汉字与世界", stage=1) for entry in parsed.entries)
    result = io.write(
        WriteRequest(
            SourceDescriptor(str(target)),
            format_id,
            entries,
            1,
            RequestContext("test"),
            source_snapshot=parsed.source_snapshot,
            options=(("output_encoding", "cp1252"),),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    reparsed = io.parse(ParseRequest(SourceDescriptor(str(target)), RequestContext("test"), format_id))
    assert all(entry.translation == "汉字与世界" for entry in reparsed.entries)
