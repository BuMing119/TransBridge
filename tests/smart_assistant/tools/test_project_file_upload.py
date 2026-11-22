"""Assistant file scope, transport and receipts use the workbench's real project and simulated gateway."""

from types import SimpleNamespace

import pytest

from tests.ui.operations.test_categorized_upload_bridge import (
    harness as upload_harness,
    project_fixture as upload_project,
)
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools.tool_paratranz import _register_paratranz_tools
from transbridge.smart_assistant.tools.types import ExecutionContext

harness = upload_harness
project_fixture = upload_project


@pytest.fixture
def bound(harness, monkeypatch):
    if ToolRegistry.get("upload_entries") is None:
        _register_paratranz_tools()
    monkeypatch.setattr("transbridge.paratranz.project_upload.FilePublicationGateway", harness.remote.gateway)
    app = harness.context
    app.active_version_identity = (app.active_project_id, app.active_variant_id)
    snapshot = harness.session._capture()
    source, _ = snapshot.sources
    app.project_sources = harness.services.project_lifecycle.active.project.envelope.data["sources"]
    app.slots = {s.source_id: SimpleNamespace(source_id=s.source_id, collection=s.entries) for s in snapshot.sources}
    app.active_key = source.source_id
    app.active_slot = app.slots[app.active_key]
    app.collection = app.active_slot.collection
    ctx = ExecutionContext(app_context=app, request_context=harness.request_context)
    request = SimpleNamespace(source_message_ids=["input"], execution_version_json="")
    apply_captured_scope(request, [{"message_id": "input", "selection": {"selected_entry_ids": []}}], ctx)
    assert ctx.active_slot is None and ctx.collection is app.collection
    scope = {"source_ids": [source.source_id], "mode": "both", "categorized": True}
    return ctx, scope, harness.remote


def test_file_preview_then_exact_confirmed_file_upload(bound):
    ctx, args, remote = bound
    compare = ToolRegistry.get("compare_with_remote")
    upload = ToolRegistry.get("upload_entries")
    preview = compare.execute(args, ctx)
    assert preview.success, preview.message
    (first,) = preview.data["files"]
    assert first["source_changed"] and not first["exists"] and not remote.writes
    assert upload.permission == "write" and upload.require_confirmation and upload.is_long_running
    result = upload.execute({**args, "file_keys": [first["file_key"]]}, ctx)
    assert result.success, result.message
    assert result.data["success_count"] == 1
    assert {row[2] for row in remote.writes} == {first["path"]}
    before = list(remote.writes)
    again = compare.execute(args, ctx)
    assert again.success, again.message
    (repeated,) = again.data["files"]
    assert repeated["exists"] and not repeated["source_changed"] and repeated["renames"] == 0
    assert remote.writes == before


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"project_id": 42},
        {"entry_ids": ["key"]},
        {"force_overwrite": False},
        {"mode": "both"},
        {"mode": "bad", "file_keys": ["unknown"]},
    ],
)
def test_old_or_unconfirmed_file_scope_never_opens_remote(bound, args):
    ctx, _, remote = bound
    result = ToolRegistry.get("upload_entries").execute(args, ctx)
    assert not result.success
    assert remote.reads == remote.writes == []


def test_selected_entries_cannot_be_widened_to_a_whole_file(bound):
    ctx, args, remote = bound
    request = SimpleNamespace(source_message_ids=["input"], execution_version_json="")
    selection = {"selected_entry_ids": [ctx.collection[0].key]}
    apply_captured_scope(request, [{"message_id": "input", "selection": selection}], ctx)
    result = ToolRegistry.get("upload_entries").execute({**args, "file_keys": ["any"]}, ctx)
    assert not result.success and "选区" in result.message
    assert not remote.reads and not remote.writes


def test_missing_remote_file_skipped_in_translation_mode_is_incomplete(bound):
    ctx, args, remote = bound
    args = {**args, "mode": "trans_safe"}
    preview = ToolRegistry.get("compare_with_remote").execute(args, ctx)
    key = preview.data["files"][0]["file_key"]
    result = ToolRegistry.get("upload_entries").execute({**args, "file_keys": [key]}, ctx)
    assert not result.success and not result.partial and not remote.writes
    assert result.data["pending_count"] == 1 and result.data["files"][0]["status"] == "skipped"


def test_confirmed_source_and_uncertain_translation_report_partial_without_replay(bound, monkeypatch):
    ctx, args, remote = bound
    attempts = []

    def lose_translation(*args, **kwargs):
        attempts.append(args)
        raise TimeoutError("response unavailable")

    monkeypatch.setattr(remote, "translate", lose_translation)
    preview = ToolRegistry.get("compare_with_remote").execute(args, ctx)
    key = preview.data["files"][0]["file_key"]
    result = ToolRegistry.get("upload_entries").execute({**args, "file_keys": [key]}, ctx)
    assert not result.success and result.partial and len(attempts) == 1
    assert result.data["success_count"] == 0 and result.data["failed_count"] == 1
    file = result.data["files"][0]
    assert file["status"] == "uncertain" and file["source_applied"] and not file["translation_applied"]


def test_cancelled_upload_makes_no_remote_request(bound):
    ctx, args, remote = bound
    ctx.cancellation_token = CancellationToken()
    ctx.cancellation_token._cancel("stopped")
    with pytest.raises(TaskCancelled):
        ToolRegistry.get("upload_entries").execute({**args, "file_keys": ["any"]}, ctx)
    assert not remote.reads and not remote.writes
