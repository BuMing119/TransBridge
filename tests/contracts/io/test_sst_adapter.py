from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import (
    CapabilityLevel,
    FormatId,
    ParseRequest,
    SourceDescriptor,
    TranslationIoUseCase,
    WriteRequest,
    default_format_catalog,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.sst_adapter import SstAdapter
from transbridge.application.io.sst_export import SstExportCancelled, SstExportError
from transbridge.converter.sst_entries import create_sst_entry, document_entries
from transbridge.parser.xt.sst_codec import SstDocument, SstRecord
from transbridge.parser.xt.sst_parser import SST_Entry


def _record(**changes):
    values = dict(
        list_index=0,
        string_id=0,
        form_id=0x800,
        record_type="NPC_",
        field_type="FULL",
        index=0,
        index_max=2,
        edid_hash=0x12345678,
        collab_id=7,
        params=0x40,
        original="Hello",
        translation="你好",
    )
    values.update(changes)
    return SstRecord(**values)


def _parse(tmp_path, format_id=FormatId.SST_SSU9, *, records=None):
    magic = b"SSU8" if format_id is FormatId.SST_SSU8 else b"SSU9"
    masters = () if magic == b"SSU8" else ("Base.esm", "Example.esp")
    records = (_record(), _record(list_index=2, translation="")) if records is None else records
    document = SstDocument(magic, 0xA5, masters, ((7, "Reviewer"),), records)
    path = tmp_path / "input.sst"
    path.write_bytes(document.to_bytes())
    request = ParseRequest(SourceDescriptor(str(path)), RequestContext("sst-test"), format_id)
    parsed = TranslationIoUseCase().parse(request)
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return path, document, parsed


def _write_request(tmp_path, parsed, entries=None, **changes):
    values = dict(
        target=SourceDescriptor(str(tmp_path / "output.sst")),
        format_id=parsed.format_id,
        entries=parsed.entries if entries is None else entries,
        variant_revision=0,
        context=RequestContext("sst-test"),
        source_snapshot=parsed.source_snapshot,
    )
    values.update(changes)
    return WriteRequest(**values)


@pytest.mark.parametrize("format_id", [FormatId.SST_SSU8, FormatId.SST_SSU9])
def test_original_format_write_preserves_complete_document_and_empty_translations(tmp_path, format_id):
    path, document, parsed = _parse(tmp_path, format_id)
    request = _write_request(tmp_path, parsed)
    result = TranslationIoUseCase().write(request)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert (tmp_path / "output.sst").read_bytes() == path.read_bytes() == document.to_bytes()
    assert parsed.capability.write is CapabilityLevel.EXPERIMENTAL
    assert dict(parsed.source_snapshot.metadata)["sst_codec_version"] == 2
    assert [entry.key for entry in parsed.entries] == ["sst-v2:0", "sst-v2:1"]


def test_duplicate_native_locators_remain_independent_and_only_selected_record_changes(tmp_path):
    source_record = _record()
    path, document, parsed = _parse(tmp_path, records=(source_record, source_record))
    edited = replace(parsed.entries[1], translation="Changed 😀")
    request = _write_request(tmp_path, parsed, (edited,))
    result = TranslationIoUseCase().write(request)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    actual = SstDocument.from_bytes((tmp_path / "output.sst").read_bytes())
    expected = replace(document, records=(source_record, replace(source_record, translation="Changed 😀")))
    assert actual == expected
    assert path.read_bytes() == document.to_bytes()


@pytest.mark.parametrize("stage", [-1, 0, 1, 2, 3, 5, 9])
def test_bilingual_write_preserves_explicit_translation_and_native_status(tmp_path, stage):
    _, document, parsed = _parse(tmp_path)
    edited = replace(parsed.entries[0], stage=stage, translation="Draft")
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed, (edited,)))
    assert result.outcome is OperationOutcome.COMPLETED
    actual = SstDocument.from_bytes((tmp_path / "output.sst").read_bytes())
    assert actual.records[0] == replace(document.records[0], translation="Draft")
    assert actual.records[1] == document.records[1]


@pytest.mark.parametrize("stage,expected", [(1, ""), (0, ""), (9, "你好")])
def test_empty_edit_obeys_locked_source_preservation_only(tmp_path, stage, expected):
    _, document, parsed = _parse(tmp_path)
    edited = replace(parsed.entries[0], stage=stage, translation="")
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed, (edited,)))
    assert result.outcome is OperationOutcome.COMPLETED
    actual = SstDocument.from_bytes((tmp_path / "output.sst").read_bytes())
    assert actual.records[0] == replace(document.records[0], translation=expected)


@pytest.mark.parametrize("legacy_part", ["snapshot", "entry"])
def test_legacy_identity_requires_reimport_without_touching_target(tmp_path, legacy_part):
    _, _, parsed = _parse(tmp_path)
    request = _write_request(tmp_path, parsed)
    if legacy_part == "snapshot":
        request = replace(request, source_snapshot=replace(request.source_snapshot, metadata=()))
    else:
        request = replace(request, entries=(replace(parsed.entries[0], metadata=()),))
    target = tmp_path / "output.sst"
    target.write_bytes(b"existing target")
    result = TranslationIoUseCase().write(request)
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SST_REIMPORT_REQUIRED"
    assert "重新导入" in result.diagnostics[0].message
    assert target.read_bytes() == b"existing target"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sst.record_index", 999),
        ("sst.record_index", True),
        ("sst.record_signature", "bad"),
        ("sst.source_sha256", "bad"),
    ],
)
def test_tampered_record_binding_cannot_redirect_write(tmp_path, field, value):
    _, _, parsed = _parse(tmp_path)
    entry = parsed.entries[0]
    metadata = dict(entry.metadata)
    metadata[field] = value
    edited = replace(entry, metadata=tuple(metadata.items()))
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed, (edited,)))
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SST_IDENTITY_CONFLICT"
    assert not (tmp_path / "output.sst").exists()


@pytest.mark.parametrize("change", ["namespace", "original", "duplicate", "stage"])
def test_invalid_write_entries_are_rejected_before_output(tmp_path, change):
    _, _, parsed = _parse(tmp_path)
    entry = parsed.entries[0]
    entries = (entry,)
    if change == "namespace":
        entries = (replace(entry, entry_key=EntryKey(SourceNamespace("wrong"), entry.key)),)
    elif change == "original":
        entries = (replace(entry, original="Other source"),)
    elif change == "duplicate":
        entries = (entry, entry)
    else:
        entries = (replace(entry, stage=4),)
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed, entries))
    assert result.outcome is OperationOutcome.FAILED
    assert not (tmp_path / "output.sst").exists()


def test_hydrated_snapshot_survives_source_removal_and_direct_source_change_is_rejected(tmp_path):
    path, document, parsed = _parse(tmp_path)
    path.write_bytes(b"modified external source")
    request = _write_request(tmp_path, parsed)
    failure = TranslationIoUseCase().write(request)
    assert failure.diagnostics[0].code == "SOURCE_FINGERPRINT_CONFLICT"
    path.unlink()
    result = TranslationIoUseCase().write(replace(request, options=(("source_authority", "hydration-v2"),)))
    assert result.outcome is OperationOutcome.COMPLETED
    assert (tmp_path / "output.sst").read_bytes() == document.to_bytes()


@pytest.mark.parametrize("when", ["before", "after-render", "after-flush"])
def test_cancelled_write_preserves_target_and_removes_owned_temporary_files(tmp_path, monkeypatch, when):
    _, _, parsed = _parse(tmp_path)
    target = tmp_path / "output.sst"
    target.write_bytes(b"existing")
    cancellation = SimpleNamespace(is_cancelled=when == "before")
    adapter = SstAdapter(parsed.format_id)
    if when == "after-render":
        original_render = adapter.render_document

        def cancel_after_render(request):
            result = original_render(request)
            cancellation.is_cancelled = True
            return result

        monkeypatch.setattr(adapter, "render_document", cancel_after_render)
    elif when == "after-flush":
        import transbridge.application.io.sst_adapter as module

        monkeypatch.setattr(module.os, "fsync", lambda _: setattr(cancellation, "is_cancelled", True))
    result = adapter.write(_write_request(tmp_path, parsed, cancellation=cancellation))
    assert result.outcome is OperationOutcome.CANCELLED
    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".output.sst.*.tmp"))


def test_failed_atomic_replace_keeps_previous_target_and_cleans_temporary(tmp_path, monkeypatch):
    import transbridge.application.io.sst_adapter as module

    _, _, parsed = _parse(tmp_path)
    target = tmp_path / "output.sst"
    target.write_bytes(b"existing")

    def fail_replace(*_):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    result = TranslationIoUseCase().write(_write_request(tmp_path, parsed))
    assert result.outcome is OperationOutcome.FAILED
    assert "injected replacement failure" in result.diagnostics[0].message
    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".output.sst.*.tmp"))


def test_byte_parse_and_shared_mapper_use_the_same_identity(tmp_path):
    _, document, parsed = _parse(tmp_path)
    namespace = SourceNamespace(dict(parsed.source_snapshot.metadata)["source_namespace"])
    assert document_entries(document, namespace) == parsed.entries
    facade = create_sst_entry(SST_Entry.from_record(document.records[0], 0), namespace)
    assert facade.identity == parsed.entries[0].identity
    assert dict(facade.metadata)["sst.record_signature"] == dict(parsed.entries[0].metadata)["sst.record_signature"]


def test_all_sst_capabilities_remain_experimental():
    snapshots = default_format_catalog().capability_snapshot()
    for snapshot in snapshots:
        if snapshot.format_id not in (FormatId.SST_SSU8, FormatId.SST_SSU9):
            continue
        for name in ("read", "write", "round_trip", "publish", "fidelity"):
            assert getattr(snapshot.capability, name) is CapabilityLevel.EXPERIMENTAL


def test_target_extension_is_checked_and_staging_keeps_final_display_name(tmp_path):
    _, document, parsed = _parse(tmp_path)
    wrong = _write_request(tmp_path, parsed, target=SourceDescriptor(str(tmp_path / "wrong.esp")))
    result = TranslationIoUseCase().write(wrong)
    assert result.diagnostics[0].code == "SST_TARGET_EXTENSION"
    staged = replace(wrong, target=SourceDescriptor(str(tmp_path / ".output.stage"), display_name="output.sst"))
    result = TranslationIoUseCase().write(staged)
    assert result.outcome is OperationOutcome.COMPLETED
    assert (tmp_path / ".output.stage").read_bytes() == document.to_bytes()


def _plugin_write_request(tmp_path):
    from tests.plugin_fixtures import write_plugin
    from transbridge.application.io import SsePluginAdapter

    source = write_plugin(tmp_path / "Example.esp", [(0x800, "NpcEditorID", "Npc name")])
    parsed = SsePluginAdapter().parse(ParseRequest(SourceDescriptor(str(source)), RequestContext("sst-test")))
    assert parsed.outcome is OperationOutcome.COMPLETED
    return WriteRequest(
        SourceDescriptor(str(tmp_path / "output.sst")),
        FormatId.SST_SSU9,
        tuple(replace(entry, translation="译文", stage=1) for entry in parsed.entries),
        0,
        RequestContext("sst-test"),
        source_snapshot=parsed.source_snapshot,
    )


def test_plugin_export_public_adapter_produces_complete_ssu9(tmp_path):
    request = _plugin_write_request(tmp_path)
    result = TranslationIoUseCase().write(request)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    document = SstDocument.from_bytes((tmp_path / "output.sst").read_bytes())
    assert document.magic == b"SSU9"
    assert document.masters == ("example.esp",)
    assert document.records[0].original == "Npc name"
    assert document.records[0].translation == "译文"
    assert (tmp_path / "Example.esp").read_bytes() == request.source_snapshot.content


def test_plugin_export_cannot_overwrite_original_plugin_even_with_spoofed_display_name(tmp_path):
    request = _plugin_write_request(tmp_path)
    request = replace(request, target=SourceDescriptor(request.source_snapshot.source.uri, display_name="output.sst"))
    result = TranslationIoUseCase().write(request)
    assert result.diagnostics[0].code == "SST_TARGET_IS_SOURCE_PLUGIN"
    assert (tmp_path / "Example.esp").read_bytes() == request.source_snapshot.content


def test_plugin_export_does_not_guess_ssu8_output(tmp_path):
    request = replace(_plugin_write_request(tmp_path), format_id=FormatId.SST_SSU8)
    result = TranslationIoUseCase().write(request)
    assert result.diagnostics[0].code == "FORMAT_MISMATCH"
    assert not (tmp_path / "output.sst").exists()


@pytest.mark.parametrize("operation", ["validate_write", "write"])
@pytest.mark.parametrize("cancel", [True, False])
def test_plugin_builder_errors_retain_codes_and_cancellation(tmp_path, monkeypatch, operation, cancel):
    import transbridge.application.io.sst_export as exporter

    request = _plugin_write_request(tmp_path)

    def fail_builder(_):
        if cancel:
            raise SstExportCancelled
        raise SstExportError("SST_EXPORT_TEST_FAILURE", "明确的来源诊断")

    monkeypatch.setattr(exporter, "build_sst_from_plugin", fail_builder)
    result = getattr(SstAdapter(FormatId.SST_SSU9), operation)(request)
    if cancel:
        assert result.outcome is OperationOutcome.CANCELLED
    else:
        assert result.outcome is OperationOutcome.FAILED
        assert result.diagnostics[0].code == "SST_EXPORT_TEST_FAILURE"
    assert not (tmp_path / "output.sst").exists()
