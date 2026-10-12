"""Real binary publication must preserve physical positions across changed identities."""

from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.parser.test_original_match_plugin import quest_plugin
from tests.plugin_fixtures import _field, _record
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter, WriteRequest
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import (
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
)
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.strings_file import PluginStringsLookup


def _request(source, target, translations):
    context = RequestContext("plugin-fidelity", run_id="plugin-fidelity")
    parsed = SsePluginAdapter().parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    assert parsed.outcome is OperationOutcome.COMPLETED
    entries = tuple(
        replace(entry, translation=translations[entry.original], stage=1)
        for entry in parsed.entries
        if entry.original in translations
    )
    return WriteRequest(
        SourceDescriptor(str(target)), FormatId.PLUGIN_SSE, entries, 1, context, source_snapshot=parsed.source_snapshot
    )


def _publish(request, renderer=None):
    adapter = SsePluginAdapter()
    filesystem = OsPublishFilesystem()
    return PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(request.target.uri),
        renderer=renderer or FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard(request.context.run_id),
    )


def _texts(path, localized=False):
    plugin = SSEPluginWithContext.from_file(path)
    lookup = PluginStringsLookup.from_plugin(path) if localized else None
    return [entry.string for entry, _ in plugin.extract_string_pairs_with_context(strings_lookup=lookup)]


@pytest.mark.parametrize(
    "translations,expected",
    [
        ({"First": "Translated first", "Second": "Translated second"}, ["Translated first", "Translated second"]),
        ({"First": "Same", "Second": "Same"}, ["Same", "Same"]),
        ({"First": "Second", "Second": "First"}, ["Second", "First"]),
        ({"First": "Changed"}, ["Changed", "Second"]),
        ({"First": "Second"}, ["Second", "Second"]),
    ],
)
def test_original_qualified_logs_publish_at_source_positions(tmp_path, translations, expected):
    source = quest_plugin(tmp_path / "source.esp", ["First", "Second"])
    original = source.read_bytes()
    target = tmp_path / "译文.esp"
    request = _request(source, target, translations)
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            request.source_snapshot,
            request.format_id,
            tuple(entry.snapshot() for entry in request.entries),
            str(target),
            1,
            request.context,
        )
    )
    assert checked.ready
    result = HydratedWriteWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=request.context.run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(request.context.run_id),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _texts(target) == expected
    assert source.read_bytes() == original


def test_preserves_skipped_duplicates_and_empty_strings(tmp_path):
    source = quest_plugin(tmp_path / "source.esp", ["Duplicate", "Duplicate", "", "Unique", "Other"])
    target = tmp_path / "published.esp"
    request = _request(source, target, {"Unique": "Translated"})
    result = _publish(request)
    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert _texts(target) == ["Duplicate", "Duplicate", "", "Translated", "Other"]


@pytest.mark.parametrize("corruption", ["swap", "unrequested", "duplicate", "empty", "extra", "remove"])
def test_rejects_misplaced_translations_and_changes_outside_request(tmp_path, corruption):
    source = quest_plugin(tmp_path / "source.esp", ["First", "Second", "Duplicate", "Duplicate", "", "Other"])
    target = tmp_path / "published.esp"
    request = _request(source, target, {"First": "Translated first", "Second": "Translated second"})

    class CorruptingRenderer(FormatAdapterRenderer):
        def render(self, request, staging_path):
            result = super().render(request, staging_path)
            texts = ["Translated first", "Translated second", "Duplicate", "Duplicate", "", "Other"]
            if corruption == "swap":
                texts[0], texts[1] = texts[1], texts[0]
            elif corruption == "unrequested":
                texts[-1] = "Changed without request"
            elif corruption == "duplicate":
                texts[2] = "Changed skipped entry"
            elif corruption == "empty":
                texts[4] = "Changed empty entry"
            elif corruption == "extra":
                texts.append("Extra")
            else:
                texts.pop()
            quest_plugin(Path(staging_path), texts)
            return result

    result = _publish(request, CorruptingRenderer(SsePluginAdapter()))
    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "FIDELITY_MISMATCH"
    assert not target.exists()
    assert not list(tmp_path.glob("*.stage"))


def _localized_quest(path, identifiers=(1, 2, 3)):
    stage = b"INDX" + struct.pack("<HHBB", 4, 10, 0, 0)
    logs = b"".join(b"QSDT\x01\x00\x00CNAM\x04\x00" + struct.pack("<I", value) for value in identifiers)
    quest = _record(b"QUST", 0x800, _field(b"EDID", "Quest") + stage + logs)
    header = bytearray(_record(b"TES4", 0, b""))
    header[8:12] = struct.pack("<I", 0x80)
    path.write_bytes(header + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(quest), b"QUST", 0, 0, 0, 0) + quest)
    strings = path.parent / "Strings"
    strings.mkdir()
    values = [b"First\0", b"Second\0", b"Other\0", b"Unreferenced\0"]
    payload = bytearray()
    offsets = bytearray()
    for index, value in enumerate(values, 1):
        offsets.extend(struct.pack("<II", index, len(payload)))
        payload.extend(struct.pack("<I", len(value)) + value)
    (strings / f"{path.stem}_English.dlstrings").write_bytes(
        struct.pack("<II", len(values), len(payload)) + offsets + payload
    )
    return path


@pytest.mark.parametrize(
    "translations",
    [
        {"First": "Translated first", "Second": "Translated second"},
        {"First": "Same", "Second": "Same"},
        {"First": "Second", "Second": "First"},
        {"First": "Second"},
    ],
)
def test_localized_logs_publish_and_preserve_unrequested_companions(tmp_path, translations):
    source = _localized_quest(tmp_path / "source.esp")
    target = tmp_path / "published.esp"
    request = _request(source, target, translations)
    original = source.read_bytes()
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(request.source_snapshot, request.format_id, request.entries, str(target), 1, request.context)
    )
    assert checked.ready
    result = HydratedWriteWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=request.context.run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(request.context.run_id),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert _texts(target, localized=True) == [translations.get(value, value) for value in ["First", "Second", "Other"]]
    assert PluginStringsLookup.from_plugin(target).get(4) == "Unreferenced"
    assert source.read_bytes() == original


def test_all_real_plugin_entries_can_be_published_from_hydration(tmp_path):
    source = Path("tests/parser/data/sample.esp")
    context = RequestContext("plugin-fidelity", run_id="plugin-fidelity")
    parsed = SsePluginAdapter().parse(ParseRequest(SourceDescriptor(str(source)), context, FormatId.PLUGIN_SSE))
    entries = tuple(
        replace(entry, translation=f"Translated {index}", stage=1) for index, entry in enumerate(parsed.entries)
    )
    assert any(entry.requires_original_match for entry in entries)
    target = tmp_path / "translated.esp"
    result = _publish(
        WriteRequest(
            SourceDescriptor(str(target)),
            FormatId.PLUGIN_SSE,
            entries,
            1,
            context,
            source_snapshot=parsed.source_snapshot,
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert target.exists()


def test_localized_fidelity_rejects_swapped_texts_at_different_ids(tmp_path):
    source = _localized_quest(tmp_path / "source.esp")
    target = tmp_path / "published.esp"
    request = _request(source, target, {"First": "Alpha", "Second": "Bravo"})
    adapter = SsePluginAdapter()
    assert adapter.write(request).outcome is OperationOutcome.COMPLETED
    strings = tmp_path / "Strings" / "published_English.dlstrings"
    content = strings.read_bytes()
    strings.write_bytes(content.replace(b"Alpha", b"_____").replace(b"Bravo", b"Alpha").replace(b"_____", b"Bravo"))

    report = FormatRoundTripValidator(adapter, OsPublishFilesystem()).validate(request, str(target))

    assert not report.valid
    assert report.code == "FIDELITY_MISMATCH"


def test_partial_localized_request_updates_every_reference_to_shared_id(tmp_path):
    source = _localized_quest(tmp_path / "source.esp")
    records = b"".join(
        _record(b"NPC_", index, _field(b"EDID", editor_id) + b"FULL\x04\x00" + struct.pack("<I", string_id))
        for index, editor_id, string_id in [(0x800, "FirstNpc", 1), (0x801, "SecondNpc", 1), (0x802, "OtherNpc", 3)]
    )
    source.write_bytes(
        source.read_bytes()[:24] + struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), b"NPC_", 0, 0, 0, 0) + records
    )
    target = tmp_path / "published.esp"
    request = _request(source, target, {"First": "Changed shared text"})
    request = replace(request, entries=request.entries[:1])
    adapter = SsePluginAdapter()
    assert adapter.write(request).outcome is OperationOutcome.COMPLETED

    report = FormatRoundTripValidator(adapter, OsPublishFilesystem()).validate(request, str(target))

    assert report.valid, report.message
    assert _texts(target, localized=True) == ["Changed shared text", "Changed shared text", "Other"]
