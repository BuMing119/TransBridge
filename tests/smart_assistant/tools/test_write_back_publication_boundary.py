from __future__ import annotations

from dataclasses import replace
import os
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_safe_write_back import manager as manager_fixture
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SsePluginAdapter
from transbridge.application.io.publish import OsPublishFilesystem
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools import tool_writer

manager = manager_fixture
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def plugin_case(tmp_path):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "First", "Original"), (0x801, "Second", "Unchanged")])
    request = RequestContext("write-test", authorized_roots=(str(tmp_path),))
    parsed = SsePluginAdapter().parse(ParseRequest(SourceDescriptor(str(source)), request, FormatId.PLUGIN_SSE))
    collection = TranslationEntryCollection(
        tuple(replace(entry, translation="Translated " + entry.original, stage=1) for entry in parsed.entries)
    )
    context = SimpleNamespace(
        active_slot=SimpleNamespace(source_snapshot=parsed.source_snapshot, collection=collection),
        request_context=request,
    )
    return SimpleNamespace(source=source, output=tmp_path / "output.esp", context=context)


def _finish(manager, started, expected):
    assert started.success, started.message
    handle = manager.get_handle(started.data["task_id"])
    deadline = monotonic() + 5
    while handle._thread.is_alive() and monotonic() < deadline:
        QTest.qWait(5)
    _APP.processEvents()
    assert not handle._thread.is_alive()
    assert manager.get_status(started.data["task_id"])["status"] == expected
    assert handle.notified
    return handle


def _texts(path):
    result = SsePluginAdapter().parse(
        ParseRequest(SourceDescriptor(str(path)), RequestContext("verify"), FormatId.PLUGIN_SSE)
    )
    assert result.outcome is OperationOutcome.COMPLETED
    return [entry.original for entry in result.entries]


def test_output_plugin_is_never_used_as_a_transfer_template(plugin_case, manager):
    case = plugin_case
    write_plugin(case.output, [(0x800, "First", "Changed target"), (0x802, "Added", "New target record")])
    before = case.output.read_bytes()
    source_before = case.source.read_bytes()
    started = tool_writer._tool_write_back({"target": "esp", "path": str(case.output), "overwrite": True}, case.context)
    handle = _finish(manager, started, "completed")

    assert _texts(case.output) == ["Translated Original", "Translated Unchanged"]
    assert case.source.read_bytes() == source_before
    assert handle.result["outcome"] == "completed"
    assert handle.result["artifacts"] == [str(case.output)]
    assert handle.result["backup_path"]
    assert next(case.output.parent.glob(".output.esp.*.bak")).read_bytes() == before


@pytest.mark.parametrize("mutation", ["missing", "format", "content"])
def test_invalid_source_snapshot_rejects_before_task_registration(plugin_case, manager, mutation):
    case = plugin_case
    snapshot = case.context.active_slot.source_snapshot
    case.context.active_slot.source_snapshot = (
        None
        if mutation == "missing"
        else replace(
            snapshot,
            **({"format_id": FormatId.XML_XT} if mutation == "format" else {"content": None, "lease_id": "unhydrated"}),
        )
    )
    result = tool_writer._tool_write_back({"target": "esp", "path": str(case.output)}, case.context)
    assert not result.success
    assert result.error_code == ("SOURCE_SNAPSHOT_BOUND" if mutation == "content" else "SOURCE_SNAPSHOT_REQUIRED")
    assert not manager.list_all()
    assert not case.output.exists()


def test_missing_source_locator_fails_entire_publication(plugin_case, manager):
    case = plugin_case
    entries = tuple(case.context.active_slot.collection)
    # A valid sibling must not be published after silently dropping this stale locator.
    case.context.active_slot.collection = TranslationEntryCollection((
        replace(entries[0], original="Stale"),
        entries[1],
    ))
    started = tool_writer._tool_write_back({"target": "esp", "path": str(case.output)}, case.context)
    handle = _finish(manager, started, "failed")
    assert not case.output.exists()
    assert handle.result["outcome"] == "failed"
    assert handle.result["artifacts"] == []
    assert handle.result["diagnostics"]
    assert "SOURCE_LOCATOR_NOT_FOUND" in str(handle.result["diagnostics"])


@pytest.mark.parametrize("after_replace", [False, True])
def test_publication_fault_retains_outcome_artifacts_and_diagnostics(plugin_case, manager, monkeypatch, after_replace):
    case = plugin_case

    def fail_sync(*_args):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(OsPublishFilesystem, "fsync_directory" if after_replace else "fsync_file", fail_sync)
    started = tool_writer._tool_write_back({"target": "esp", "path": str(case.output)}, case.context)
    handle = _finish(manager, started, "failed")
    assert case.output.exists() is after_replace
    assert handle.result["outcome"] == ("partial" if after_replace else "failed")
    assert handle.result["artifacts"] == ([str(case.output)] if after_replace else [])
    assert handle.result["diagnostics"][0]["code"] == (
        "DIRECTORY_FSYNC_FAILED" if after_replace else "PUBLISH_IO_FAILED"
    )
    assert "written_count" not in handle.result


def test_post_publish_undo_failure_retains_already_committed_artifact(plugin_case, manager, monkeypatch):
    case = plugin_case

    def fail_receipt(_context, _targets, mutation):
        mutation()
        raise OSError("undo receipt could not be persisted")

    monkeypatch.setattr(tool_writer, "capture_file_command", fail_receipt)
    started = tool_writer._tool_write_back({"target": "esp", "path": str(case.output)}, case.context)
    handle = _finish(manager, started, "failed")
    assert _texts(case.output) == ["Translated Original", "Translated Unchanged"]
    assert handle.result["artifacts"] == [str(case.output)]
    assert handle.result["outcome"] == "partial"
    assert handle.result["publication_outcome"] == "completed"
    assert "undo receipt" in handle.result["post_publish_error"]
    assert "written_count" not in handle.result
