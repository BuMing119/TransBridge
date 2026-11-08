"""SSU9 creation from actual plugin snapshots and physical field identities."""

from dataclasses import replace
import struct

import pytest

from tests.plugin_fixtures import _field, _record, write_plugin
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.publish import (
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
)
from transbridge.application.io.sst_adapter import SstAdapter
from transbridge.application.io.sst_export import (
    SstExportCancelled,
    SstExportError,
    build_sst_from_plugin,
    sanitize_form_id,
)
from transbridge.parser.strings_file import SkyrimStringsWriter
from transbridge.parser.xt.sst_codec import SstDocument, sst_hash


def _plugin(path, groups, *, masters=(), localized=False):
    header = bytearray(_record(b"TES4", 0, b"".join(_field(b"MAST", name) for name in masters)))
    if localized:
        header[8:12] = struct.pack("<I", 0x80)
    data = b"".join(
        struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), kind, 0, 0, 0, 0) + records for kind, records in groups
    )
    path.write_bytes(header + data)
    return path


def _request(source):
    parsed = SsePluginAdapter().parse(ParseRequest(SourceDescriptor(str(source)), RequestContext("test")))
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return WriteRequest(
        SourceDescriptor(str(source.with_suffix(".sst"))),
        FormatId.SST_SSU9,
        tuple(replace(entry, translation=f"译文 {i}", stage=1) for i, entry in enumerate(parsed.entries)),
        0,
        RequestContext("test", run_id="sst-export"),
        source_snapshot=parsed.source_snapshot,
    )


def test_build_without_template_or_live_source_uses_actual_editor_id(tmp_path):
    source = write_plugin(tmp_path / "Example.esp", [(0x800, "NpcEditorID", "Npc name")])
    request = _request(source)
    source.unlink()
    result = build_sst_from_plugin(request)
    assert result.magic == b"SSU9"
    assert result.masters == ("example.esp",)
    record = result.records[0]
    assert (record.string_id, record.form_id, record.index, record.index_max) == (0, 0x800, 0, 0)
    assert (record.record_type, record.field_type, record.list_index) == ("NPC_", "FULL", 0)
    assert record.edid_hash == sst_hash("NpcEditorID")
    assert record.translation == "译文 0"
    assert SstDocument.from_bytes(result.to_bytes()) == result
    assert not source.with_suffix(".sst").exists()  # Builder has no publication side effect.


def test_atomic_publication_rebuilds_after_source_parser_retirement(tmp_path):
    source = write_plugin(tmp_path / "Example.esp", [(0x800, "NpcEditorID", "Npc name")])
    request = _request(source)
    source.unlink()
    adapter = SstAdapter(FormatId.SST_SSU9)
    filesystem = OsPublishFilesystem()
    result = PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(request.target.uri),
        renderer=FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard(request.context.run_id),
    )
    assert result.outcome is OperationOutcome.COMPLETED, (result.code, result.message)
    assert result.validation.fidelity_valid
    assert SstDocument.from_bytes(source.with_suffix(".sst").read_bytes()).records[0].translation == "译文 0"
    assert not tuple(tmp_path.glob("*.stage"))


def test_cancel_during_sst_fidelity_reports_cancelled_and_cleans_stage(tmp_path):
    class Token:
        is_cancelled = False

    class CancellingValidator:
        def validate(self, request, staging_path):
            token.is_cancelled = True
            return FormatRoundTripValidator(adapter, filesystem).validate(request, staging_path)

    token = Token()
    source = write_plugin(tmp_path / "Example.esp", [(0x800, "NpcEditorID", "Npc name")])
    request = replace(_request(source), cancellation=token)
    output = source.with_suffix(".sst")
    output.write_bytes(b"existing target")
    adapter = SstAdapter(FormatId.SST_SSU9)
    filesystem = OsPublishFilesystem()
    result = PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(request.target.uri),
        renderer=FormatAdapterRenderer(adapter),
        validator=CancellingValidator(),
        commit_guard=ImmediateCommitGuard(request.context.run_id),
    )
    assert result.outcome is OperationOutcome.CANCELLED
    assert result.code == "PUBLISH_CANCELLED"
    assert output.read_bytes() == b"existing target"
    assert not tuple(tmp_path.glob("*.stage"))


def test_master_order_and_real_no_edid_fallback(tmp_path):
    source = _plugin(
        tmp_path / "Patch.esp",
        [(b"NPC_", _record(b"NPC_", 0x02000800, _field(b"FULL", "Name")))],
        masters=("Skyrim.esm", "Update.esm"),
    )
    result = build_sst_from_plugin(_request(source))
    assert result.masters == ("skyrim.esm", "update.esm", "patch.esp")
    assert result.records[0].edid_hash == sst_hash("[01000800]")
    assert result.records[0].form_id == 0x02000800


def test_quest_indices_follow_physical_field_order_not_business_index(tmp_path):
    stage = b"INDX" + struct.pack("<HHBB", 4, 10, 0, 0)
    logs = b"".join(b"QSDT\x01\x00\x00" + _field(b"CNAM", text) for text in ("First", "Second", "Third"))
    source = _plugin(
        tmp_path / "Quest.esp",
        [(b"QUST", _record(b"QUST", 0x800, _field(b"EDID", "Quest") + stage + logs))],
    )
    request = _request(source)
    assert len({entry.key for entry in request.entries}) == 1
    result = build_sst_from_plugin(request)
    assert [record.index for record in result.records] == [0, 1, 2]
    assert [record.index_max for record in result.records] == [2, 2, 2]
    assert [record.list_index for record in result.records] == [1, 1, 1]
    assert [record.translation for record in result.records] == ["译文 0", "译文 1", "译文 2"]


def test_subset_retains_full_source_index_max(tmp_path):
    fields = _field(b"EDID", "Choices") + b"".join(_field(b"ITXT", text) for text in ("A", "B", "C"))
    source = _plugin(tmp_path / "Menu.esp", [(b"MESG", _record(b"MESG", 0x800, fields))])
    request = _request(source)
    result = build_sst_from_plugin(replace(request, entries=(request.entries[1],)))
    assert [(r.index, r.index_max) for r in result.records] == [(1, 2)]


def test_localized_snapshot_keeps_unsigned_string_id_bits_after_resources_removed(tmp_path):
    string_id = 0xF0000123
    data = _field(b"EDID", "LocalizedNpc") + b"FULL" + struct.pack("<HI", 4, string_id)
    source = _plugin(tmp_path / "Localized.esp", [(b"NPC_", _record(b"NPC_", 0x800, data))], localized=True)
    strings = tmp_path / "Strings"
    strings.mkdir()
    resource = strings / "Localized_English.strings"
    resource.write_bytes(SkyrimStringsWriter.to_bytes({string_id: "Original"}, False))
    request = _request(source)
    source.unlink()
    resource.unlink()
    result = build_sst_from_plugin(request)
    assert result.records[0].string_id == string_id - 0x100000000
    assert result.records[0].original == "Original"


@pytest.mark.parametrize("kind,field,list_index", [(b"LSCR", b"DESC", 0), (b"BOOK", b"DESC", 1), (b"INFO", b"NAM1", 2)])
def test_upstream_list_overrides_and_fallbacks(tmp_path, kind, field, list_index):
    source = _plugin(tmp_path / "Lists.esp", [(kind, _record(kind, 0x800, _field(field, "Original")))])
    document = build_sst_from_plugin(_request(source))
    assert document.records[0].list_index == list_index


@pytest.mark.parametrize(
    "stage,translation,expected,params",
    [
        (-1, "Changed", "Original", 0),
        (0, "Changed", "Original", 0),
        (1, "Changed", "Changed", 1),
        (2, "Changed", "Changed", 4),
        (9, "Changed", "Changed", 2),
        (9, "", "Original", 0),
    ],
)
def test_export_obeys_stage_policy(tmp_path, stage, translation, expected, params):
    request = _request(write_plugin(tmp_path / "Stages.esp", [(0x800, "Npc", "Original")]))
    request = replace(request, entries=(replace(request.entries[0], stage=stage, translation=translation),))
    record = build_sst_from_plugin(request).records[0]
    assert (record.translation, record.params) == (expected, params)


@pytest.mark.parametrize("forgery", ["foreign", "original", "string_id", "duplicate"])
def test_foreign_stale_and_duplicate_entries_are_rejected(tmp_path, forgery):
    request = _request(write_plugin(tmp_path / "Source.esp", [(0x800, "Npc", "Original")]))
    entry = request.entries[0]
    changes = {
        "foreign": {"entry_key": EntryKey(SourceNamespace("foreign"), entry.key)},
        "original": {"original": "Changed"},
        "string_id": {"string_id": 99},
        "duplicate": {},
    }[forgery]
    entries = (replace(entry, **changes),)
    if forgery == "duplicate":
        entries = entries * 2
    with pytest.raises(SstExportError):
        build_sst_from_plugin(replace(request, entries=entries))


def test_editor_id_facade_cannot_change_snapshot_hash(tmp_path):
    request = _request(write_plugin(tmp_path / "Source.esp", [(0x800, "RealEditorID", "Original")]))
    request = replace(request, entries=(replace(request.entries[0], editor_id="FakeEditorID"),))
    assert build_sst_from_plugin(request).records[0].edid_hash == sst_hash("RealEditorID")


def test_info_hash_does_not_use_the_inherited_topic_editor_id(tmp_path):
    topic = _record(b"DIAL", 0x800, _field(b"EDID", "TopicEditorID") + _field(b"FULL", "Topic"))
    info = _record(b"INFO", 0x801, _field(b"NAM1", "Response"))
    children = struct.pack("<4sIIiHHHH", b"GRUP", len(info) + 24, 0x800, 7, 0, 0, 0, 0) + info
    request = _request(_plugin(tmp_path / "Dialogue.esp", [(b"DIAL", topic + children)]))
    assert request.entries[1].editor_id == "TopicEditorID"
    document = build_sst_from_plugin(request)
    assert document.records[0].edid_hash == sst_hash("TopicEditorID")
    assert document.records[1].edid_hash == sst_hash("[00000801]")


def test_unverified_field_mapping_fails_instead_of_guessing(tmp_path):
    source = _plugin(tmp_path / "Note.esp", [(b"NOTE", _record(b"NOTE", 0x800, _field(b"TNAM", "Text")))])
    with pytest.raises(SstExportError) as error:
        build_sst_from_plugin(_request(source))
    assert error.value.code == "SST_EXPORT_FIELD_UNSUPPORTED"


def test_vmad_entry_is_explicitly_rejected(tmp_path):
    request = _request(write_plugin(tmp_path / "Source.esp", [(0x800, "Npc", "Original")]))
    request = replace(request, entries=(replace(request.entries[0], context="NPC_:VMAD"),))
    with pytest.raises(SstExportError) as error:
        build_sst_from_plugin(request)
    assert error.value.code == "SST_EXPORT_VMAD_UNSUPPORTED"


def test_physical_vmad_cannot_be_silently_omitted_by_entry_parser(tmp_path):
    fields = _field(b"EDID", "ScriptedNpc") + _field(b"FULL", "Original")
    fields += b"VMAD" + struct.pack("<HHHH", 6, 5, 2, 0)
    source = _plugin(tmp_path / "Scripted.esp", [(b"NPC_", _record(b"NPC_", 0x800, fields))])
    request = _request(source)
    assert len(request.entries) == 1  # Current plugin parsing does not expose VMAD entries.
    with pytest.raises(SstExportError) as error:
        build_sst_from_plugin(request)
    assert error.value.code == "SST_EXPORT_VMAD_UNSUPPORTED"


def test_cancellation_has_no_output(tmp_path):
    class Cancelled:
        is_cancelled = True

    request = _request(write_plugin(tmp_path / "Source.esp", [(0x800, "Npc", "Original")]))
    with pytest.raises(SstExportCancelled):
        build_sst_from_plugin(replace(request, cancellation=Cancelled()))
    assert not tmp_path.joinpath("Source.sst").exists()


@pytest.mark.parametrize(
    "value,expected",
    [
        (0x00000800, 0x00000800),
        (0x05000800, 0x01000800),
        (0xFE123ABC, 0xFE001ABC),
        (0xFE000ABC, 0xFE000ABC),
        (0xFD340123, 0xFD010123),
        (0xFD000123, 0xFD000123),
    ],
)
def test_fallback_form_id_normalization_matches_upstream(value, expected):
    assert sanitize_form_id(value) == expected
