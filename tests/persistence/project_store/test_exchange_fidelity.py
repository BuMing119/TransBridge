import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from tests.persistence.project_store.test_source_extraction import _create_project, _publish
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, Stage, TranslationIoUseCase
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.json_export import JsonExportDraft, JsonExportWorkload, preflight_json_export
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.application.io.paratranz_keys import encode_key
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.projects import TransitionTarget
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectRef, VariantRef

_FIXTURES = Path(__file__).parents[2] / "contracts" / "io" / "fixtures"
_CONTEXT = RequestContext("storage-test", run_id="export-exchange")


def _reopen_after_edits(tmp_path, source, format_id, edits):
    root = tmp_path / "data"
    path, variant = _create_project(root, source, format_id, role="migration")
    source.unlink()
    opened = ProjectStore(path)
    previous = opened.read_page(variant)
    working = ProjectWorkingCopy(opened, variant)
    for index, text, stage in edits:
        working.edit(previous[index].state.entry_key, translation=text, stage=stage)
    working.save()
    restarted = ProjectStore(path)
    entries = restarted.read_page(variant)
    project_id = restarted.summary().identity
    candidate = WorkspaceProjects(root).candidate(
        TransitionTarget(ProjectRef(project_id), VariantRef(variant, project_id))
    )
    (hydration,) = candidate.hydrations
    return previous, entries, hydration.source_snapshot


def _parse(path, format_id):
    result = TranslationIoUseCase().parse(ParseRequest(SourceDescriptor(str(path)), _CONTEXT, format_id))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    return result


def _json_export(snapshot, entries, target):
    source = HydratedWriteDraft(
        snapshot, snapshot.format_id, tuple(entry.snapshot for entry in entries), str(target), 1, _CONTEXT
    )
    checked = preflight_json_export(JsonExportDraft((source,), snapshot.format_id), HydratedWritePreflightService())
    assert checked.ready, checked.checks
    result = JsonExportWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=_CONTEXT.run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(_CONTEXT.run_id),
        )
    )
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    return json.loads(target.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("format_id", "fixture"), [(FormatId.XML_EET, "eet-small.xml"), (FormatId.XML_XT, "xt-small.xml")]
)
def test_reopened_xml_exports_duplicate_text_in_order_and_preserves_native_fields(tmp_path, format_id, fixture):
    source = tmp_path / fixture
    root = ET.parse(_FIXTURES / fixture).getroot()
    if format_id is FormatId.XML_EET:
        parent, row_tag, translation_tag = root, "ESP", "TRADUIT"
        root.find("ESP/STATUS").text = "99"
        root.find("ESP/COMMENTAIRE").text = "Native comment"
    else:
        parent, row_tag, translation_tag = root.find("Content"), "String", "Dest"
    first = parent.find(row_tag)
    first.find(translation_tag).text = "Prior translation"
    second = ET.fromstring(ET.tostring(first))
    second.find("EDID").text = "AnotherGreeting"
    parent.append(second)
    source.write_bytes(b"\xef\xbb\xbf" + ET.tostring(root, encoding="utf-8", xml_declaration=True))
    edits = ((0, "", Stage.TRANSLATED), (1, "Changed translation", Stage.REVIEWED))
    previous, entries, snapshot = _reopen_after_edits(tmp_path, source, format_id, edits)
    assert previous[0].snapshot.original == previous[1].snapshot.original
    assert previous[0].snapshot.entry_key != previous[1].snapshot.entry_key
    assert entries[0].state.translation == "" and entries[0].state.stage is Stage.TRANSLATED
    assert entries[1].state.stage is Stage.REVIEWED

    target = tmp_path / "output.xml"
    _publish(snapshot, tuple(item.snapshot for item in entries), target)

    parsed = _parse(target, format_id)
    assert [item.key for item in parsed.entries] == [item.snapshot.entry_key.local_key for item in previous]
    assert [item.original for item in parsed.entries] == [item.snapshot.original for item in previous]
    assert [item.translation for item in parsed.entries] == ["", "Changed translation"]
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    exported = ET.parse(target)
    if format_id is FormatId.XML_EET:
        assert [row.findtext("STATUS") for row in exported.findall("ESP")] == ["0", "99"]
        assert [row.findtext("COMMENTAIRE") for row in exported.findall("ESP")] == ["Native comment"] * 2
    else:
        assert exported.findtext("Params/Addon") == "fixture.esp"
        assert [row.find("REC").attrib["id"] for row in exported.findall("Content/String")] == ["0", "0"]


def test_reopened_paratranz_export_preserves_qualified_empty_key_extensions_and_remote_ids(tmp_path):
    namespace = SourceNamespace("source:test-wire")
    qualified = encode_key(EntryKey(namespace, "shared", ""), "")
    records = [
        {
            "key": "shared",
            "id": 0,
            "original": "Original text",
            "translation": "Clear this",
            "stage": 3,
            "note": "Native note",
            "metadata": {"custom": [1, True, None]},
        },
        {
            "key": qualified,
            "id": "remote-string",
            "original": "",
            "translation": "",
            "stage": 0,
            "context": "",
            "labels": ["native label"],
        },
    ]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(records), encoding="utf-8")
    edits = ((0, "", Stage.CHECKED), (1, "Empty source translation", Stage.REVIEWED))
    _previous, entries, snapshot = _reopen_after_edits(tmp_path, source, FormatId.JSON_PARATRANZ, edits)
    assert [item.snapshot.entry_key.original for item in entries] == [None, ""]
    assert [item.snapshot.context for item in entries] == [None, ""]
    target = tmp_path / "exported.json"
    expected = [
        {**records[0], "translation": "", "stage": 3},
        {**records[1], "translation": "Empty source translation", "stage": 5},
    ]
    assert _json_export(snapshot, entries, target) == expected


def test_reopened_dsd_export_preserves_duplicate_locators_with_distinct_originals(tmp_path):
    shared = {"form_id": "00000800|Quests.esp", "type": "QUST CNAM"}
    records = [
        {**shared, "original": "Beta", "string": "First"},
        {**shared, "original": "Alpha", "string": "Second"},
        {"form_id": "00000900|Quests.esp", "type": "INFO NAM1", "index": 7, "string": "Hidden"},
    ]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(records), encoding="utf-8")
    edits = ((1, "Edited", Stage.REVIEWED), (2, "Hidden", Stage.HIDDEN))
    _previous, entries, snapshot = _reopen_after_edits(tmp_path, source, FormatId.JSON_DSD, edits)
    assert entries[0].state.entry_key.local_key == entries[1].state.entry_key.local_key
    assert [item.state.entry_key.original for item in entries[:2]] == ["Beta", "Alpha"]
    assert _json_export(snapshot, entries, tmp_path / "exported.json") == [
        records[0],
        {**records[1], "string": "Edited"},
    ]


@pytest.mark.parametrize("format_id", [FormatId.STRINGS, FormatId.DLSTRINGS, FormatId.ILSTRINGS])
def test_reopened_strings_export_preserves_directory_order_and_untouched_payloads(tmp_path, format_id):
    extension = format_id.value.rsplit(".", 1)[1]
    source = tmp_path / f"input.{extension}"
    shutil.copyfile(_FIXTURES / "strings" / f"utf8-integrity.{extension}", source)
    original = _parse(source, format_id)
    edits = ((0, "Translated ✓", Stage.REVIEWED), (1, "Do not publish", Stage.HIDDEN))
    previous, entries, snapshot = _reopen_after_edits(tmp_path, source, format_id, edits)
    assert [item.snapshot.string_id for item in previous] == [item.string_id for item in original.entries]

    target = tmp_path / f"output.{extension}"
    _publish(snapshot, tuple(item.snapshot for item in entries), target)

    reparsed = _parse(target, format_id)
    assert [item.string_id for item in reparsed.entries] == [item.string_id for item in original.entries]
    assert [item.original for item in reparsed.entries] == [
        "Translated ✓",
        *(item.original for item in original.entries[1:]),
    ]
    before = dict(original.source_snapshot.metadata)["strings.records"]
    after = dict(reparsed.source_snapshot.metadata)["strings.records"]
    assert [(item.raw_payload, item.encoding) for item in after[1:]] == [
        (item.raw_payload, item.encoding) for item in before[1:]
    ]
