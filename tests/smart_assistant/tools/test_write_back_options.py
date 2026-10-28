"""Real assistant writes for explicit source selection, common directories and BSA."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_safe_write_back import manager as manager_fixture
from tests.smart_assistant.tools.test_write_back_publication_boundary import _finish
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.file_undo import FileUndoAdapter
from transbridge.application.io.operation_write import HydratedWriteWorkload
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_writer
from transbridge.smart_assistant.tools.tool_default import _tool_list_collections

manager = manager_fixture
localized = localized_fixture


def _slot(source, format_id, request):
    parsed = TranslationIoUseCase().parse(ParseRequest(SourceDescriptor(str(source)), request, format_id))
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    collection = TranslationEntryCollection(
        tuple(replace(entry, translation="Translated", stage=1) for entry in parsed.entries)
    )
    return SimpleNamespace(
        source_snapshot=parsed.source_snapshot,
        collection=collection,
        format_id=format_id,
        label=source.name,
        esp_path=str(source) if format_id is FormatId.PLUGIN_SSE else None,
    )


@pytest.fixture
def sources(tmp_path):
    request = RequestContext("writer-options", authorized_roots=(str(tmp_path),))
    plugin = write_plugin(tmp_path / "first.esp", [(0x800, "First", "Original")])
    xml = tmp_path / "second.xml"
    xml.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    slots = {
        "plugin-key": _slot(plugin, FormatId.PLUGIN_SSE, request),
        "xml-key": _slot(xml, FormatId.XML_EET, request),
    }
    context = SimpleNamespace(
        slots=slots, active_key="plugin-key", active_slot=slots["plugin-key"], request_context=request
    )
    return SimpleNamespace(root=tmp_path, request=request, ctx=context, plugin=plugin, xml=xml)


def _result(manager, started, status="completed"):
    return _finish(manager, started, status).result


@pytest.mark.parametrize(
    "format_id,target", [(FormatId.PLUGIN_SSE, "esp"), (FormatId.XML_EET, "eet"), (FormatId.XML_XT, "xt")]
)
def test_output_directory_works_for_every_existing_format_without_renaming(sources, manager, format_id, target):
    source = sources.plugin if target == "esp" else sources.xml
    if target == "xt":
        source.write_bytes(Path("tests/contracts/io/fixtures/xt-small.xml").read_bytes())
    sources.ctx.active_slot = _slot(source, format_id, sources.request)
    output = sources.root / "new" / "nested"
    started = tool_writer._tool_write_back({"target": target, "output_dir": str(output)}, sources.ctx)
    result = _result(manager, started)
    assert result["artifacts"] == [str(output / source.name)]
    assert (output / source.name).is_file()
    assert not tuple(output.glob("*_translated*"))


def test_explicit_batch_selects_exact_listed_keys_and_preserves_active_collection(sources, manager):
    listed = _tool_list_collections({}, sources.ctx).data["collections"]
    keys = [item["key"] for item in listed]
    output = sources.root / "output"
    originals = {path: path.read_bytes() for path in (sources.plugin, sources.xml)}
    started = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": keys, "output_dir": str(output)}, sources.ctx
    )
    result = _result(manager, started)
    assert set(started.data["paths"]) == {str(output / "first.esp"), str(output / "second.xml")}
    assert {item["source_key"] for item in result["outcomes"]} == set(keys)
    assert all(item["status"] == "succeeded" for item in result["outcomes"])
    assert {path.name for path in output.iterdir()} == {"first.esp", "second.xml"}
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert sources.ctx.active_key == "plugin-key"
    assert sources.ctx.active_slot is sources.ctx.slots["plugin-key"]


def test_one_explicit_inactive_source_can_be_written_without_an_active_collection(sources, manager):
    sources.ctx.active_slot = None
    output = sources.root / "output"
    started = tool_writer._tool_write_back(
        {"target": "eet", "source_keys": ["xml-key"], "output_dir": str(output)}, sources.ctx
    )
    assert "path" in started.data and "paths" not in started.data
    assert _result(manager, started)["artifacts"] == [str(output / "second.xml")]
    assert sources.ctx.active_slot is None


@pytest.mark.parametrize(
    "selection,code",
    [
        ([], "WRITE_SOURCE_SELECTION_INVALID"),
        (["plugin-key", "plugin-key"], "WRITE_SOURCE_SELECTION_DUPLICATE"),
        (["missing"], "WRITE_SOURCE_NOT_FOUND"),
        ("plugin-key", "WRITE_SOURCE_SELECTION_INVALID"),
        ([1], "WRITE_SOURCE_SELECTION_INVALID"),
    ],
)
def test_invalid_source_selection_never_registers_tasks_or_writes_files(sources, manager, selection, code):
    output = sources.root / "output"
    result = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": selection, "output_dir": str(output)}, sources.ctx
    )
    assert not result.success and result.error_code == code
    assert manager.list_all() == []
    assert not output.exists()


def test_auto_without_explicit_selection_and_batch_single_path_are_rejected(sources, manager):
    for args, code in [
        ({"target": "auto"}, "WRITE_SOURCE_SELECTION_REQUIRED"),
        (
            {"target": "auto", "source_keys": list(sources.ctx.slots), "path": str(sources.root / "one")},
            "WRITE_BATCH_PATH_INVALID",
        ),
    ]:
        result = tool_writer._tool_write_back(args, sources.ctx)
        assert not result.success and result.error_code == code
    assert manager.list_all() == []


def test_batch_checks_every_output_authorization_before_scheduling(sources, manager):
    outside = sources.root.parent / "outside-writer-grant"
    result = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(outside)}, sources.ctx
    )
    assert not result.success and result.error_code == "PATH_OUTSIDE_GRANT"
    assert manager.list_all() == []
    assert not outside.exists()


def test_invalid_member_format_or_missing_snapshot_fails_before_any_selected_source_is_written(sources, manager):
    sources.ctx.slots["xml-key"].source_snapshot = None
    output = sources.root / "output"
    result = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(output)}, sources.ctx
    )
    assert not result.success and result.error_code == "SOURCE_SNAPSHOT_REQUIRED"
    assert manager.list_all() == [] and not output.exists()


def test_batch_existing_target_is_reported_and_other_source_can_complete(sources, manager):
    output = sources.root / "output"
    output.mkdir()
    existing = output / "first.esp"
    existing.write_bytes(b"existing output")
    started = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(output)}, sources.ctx
    )
    result = _result(manager, started, "failed")
    assert result["outcome"] == "partial"
    assert [item["status"] for item in result["outcomes"]] == ["failed", "succeeded"]
    assert result["artifacts"] == [str(output / "second.xml")]
    assert existing.read_bytes() == b"existing output"


@pytest.mark.parametrize("when", ["before", "after-first"])
def test_cancelled_batch_retains_receipts_and_any_already_committed_outputs(sources, manager, monkeypatch, when):
    publish = HydratedWriteWorkload.__call__
    calls = []

    def cancel(self, run_context):
        calls.append(self._preflight.draft.target_path)
        handle = manager.get_handle(run_context.ref.job_id)
        if when == "before":
            manager.runtime.cancel(run_context.ref, handle.execution.owner)
        result = publish(self, run_context)
        if when == "after-first":
            manager.runtime.cancel(run_context.ref, handle.execution.owner)
        return result

    monkeypatch.setattr(HydratedWriteWorkload, "__call__", cancel)
    output = sources.root / "output"
    started = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(output)}, sources.ctx
    )
    result = _result(manager, started, "cancelled")
    assert len(calls) == 1
    assert result["outcome"] == ("partial" if when == "after-first" else "cancelled")
    assert len(result["outcomes"]) == 2
    assert result["outcomes"][-1]["source_key"] == "xml-key"
    assert result["outcomes"][-1]["status"] == "cancelled"
    assert (output / "first.esp").exists() is (when == "after-first")
    assert not (output / "second.xml").exists()


@pytest.mark.parametrize("packed", [False, True])
def test_ai_bsa_selection_and_file_undo_cover_exact_actual_outputs(sources, localized, manager, monkeypatch, packed):
    slot = _slot(localized.source, FormatId.PLUGIN_SSE, sources.request)
    sources.ctx.slots["localized-key"] = slot
    output = sources.root / "published"
    output.mkdir()
    plugin = output / localized.source.name
    archive = plugin.with_suffix(".bsa")
    original_plugin = b"old output plugin"
    plugin.write_bytes(original_plugin)
    if packed:
        archive.write_bytes(b"old output archive")
    evidence = {}
    capture = tool_writer.capture_file_command

    def record(context, targets, mutation):
        adapter = FileUndoAdapter(targets, backup_root=sources.root / "file-undo")
        before = adapter.capture_before()
        result = capture(context, targets, mutation)
        evidence.update(adapter=adapter, receipt=adapter.capture_after(before), targets=set(map(Path, targets)))
        return result

    monkeypatch.setattr(tool_writer, "capture_file_command", record)
    started = tool_writer._tool_write_back(
        {
            "target": "auto",
            "source_keys": ["localized-key", "xml-key"],
            "output_dir": str(output),
            "pack_bsa": packed,
            "overwrite": True,
        },
        sources.ctx,
    )
    result = _result(manager, started)
    actual = set(map(Path, result["artifacts"]))
    assert evidence["targets"] == actual
    assert archive.exists() is packed
    assert (output / "Strings").exists() is (not packed)
    assert sources.xml not in actual
    undone = evidence["adapter"].apply(evidence["receipt"])
    assert undone["status"] == "completed"
    assert plugin.read_bytes() == original_plugin
    if packed:
        assert archive.read_bytes() == b"old output archive"
    else:
        assert not tuple((output / "Strings").iterdir())
    assert not (output / "second.xml").exists()


def test_new_parameters_are_discoverable_and_admin_confirmation_remains_required(sources):
    spec = ToolRegistry.get("write_back")
    assert spec.require_confirmation
    assert {"pack_bsa", "source_keys", "output_dir"} <= set(spec.parameters["properties"])
    args = {
        "target": "auto",
        "source_keys": ["plugin-key", "xml-key"],
        "pack_bsa": False,
        "output_dir": str(sources.root / "output"),
    }
    assert InputValidationGuard().before_execute({"tool": "write_back", "args": args}, sources.ctx).allowed


def test_guard_and_writer_agree_on_creating_nested_authorized_output_directories(sources, manager):
    args = {
        "target": "auto",
        "source_keys": list(sources.ctx.slots),
        "output_dir": str(sources.root / "new" / "nested"),
    }
    assert InputValidationGuard().before_execute({"tool": "write_back", "args": args}, sources.ctx).allowed
    started = tool_writer._tool_write_back(args, sources.ctx)
    assert len(_result(manager, started)["artifacts"]) == 2


@pytest.mark.parametrize("args", [{"pack_bsa": "false"}, {"pack_bsa": 1}, {"pack_bsa": True}])
def test_invalid_or_inapplicable_bsa_selection_is_rejected_without_tasks(sources, manager, args):
    result = tool_writer._tool_write_back(
        {"target": "esp", "output_dir": str(sources.root / "output"), **args}, sources.ctx
    )
    assert not result.success
    assert result.error_code == ("WRITE_BSA_UNSUPPORTED" if args["pack_bsa"] is True else "WRITE_BSA_OPTION_INVALID")
    assert not manager.list_all()


def test_all_failed_batch_keeps_per_source_receipts(sources, manager, monkeypatch):
    from transbridge.application.io.publish import OsPublishFilesystem

    def fail_sync(*_args):
        raise OSError("injected write failure")

    monkeypatch.setattr(OsPublishFilesystem, "fsync_file", fail_sync)
    started = tool_writer._tool_write_back(
        {"target": "auto", "source_keys": list(sources.ctx.slots), "output_dir": str(sources.root / "output")},
        sources.ctx,
    )
    result = _result(manager, started, "failed")
    assert result["outcome"] == "failed" and result["artifacts"] == []
    assert {item["source_key"] for item in result["outcomes"]} == set(sources.ctx.slots)
    assert all(item["status"] == "failed" for item in result["outcomes"])


def test_all_colliding_outputs_are_rejected_before_scheduling(sources, manager):
    second = sources.root / "another" / sources.plugin.name
    second.parent.mkdir()
    write_plugin(second, [(0x900, "Other", "Other original")])
    sources.ctx.slots["another-key"] = _slot(second, FormatId.PLUGIN_SSE, sources.request)
    started = tool_writer._tool_write_back(
        {"target": "esp", "source_keys": ["plugin-key", "another-key"], "output_dir": str(sources.root / "output")},
        sources.ctx,
    )
    assert not started.success and started.error_code == "BATCH_OUTPUT_CONFLICT"
    assert len(started.failed_items) == 2
    assert not manager.list_all()
    assert not (sources.root / "output").exists()


def test_explicit_single_file_path_preserves_precedence_over_output_dir(sources, manager):
    destination = sources.root / "chosen.esp"
    output = sources.root / "unused-directory"
    started = tool_writer._tool_write_back(
        {"target": "esp", "path": str(destination), "output_dir": str(output)}, sources.ctx
    )
    assert _result(manager, started)["artifacts"] == [str(destination)]
    assert not output.exists()
