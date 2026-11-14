from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.application.projects.remote_binding import ParaTranzProjectBinding
from transbridge.application.projects.source_references import ProjectSourceReferences
from transbridge.application.tasks import TaskRuntime
from transbridge.smart_assistant.request_scope import apply_captured_scope, execution_version
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp, _tool_parse_xt
from transbridge.smart_assistant.tools.tool_writer import _tool_write_back
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.source_hydration import apply_variant_projection
from transbridge.ui.workbench.cards import write_card as write_card_module

_APP = QApplication.instance() or QApplication([])
_TRANSLATION = "安全发布的译文"
project = project_fixture
localized = localized_fixture


@pytest.fixture
def manager():
    TaskManager.reset()
    value = TaskManager()
    assert isinstance(value.runtime, TaskRuntime)
    yield value
    TaskManager.reset()
    _APP.processEvents()


def _wait_for_result(manager, result):
    assert result.success, result.message
    task_id = result.data["task_id"]
    deadline = monotonic() + 5
    terminal = {"completed", "failed", "cancelled"}
    while manager.get_status(task_id)["status"] not in terminal and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    handle = manager.get_handle(task_id)
    while handle._thread.is_alive() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    status = manager.get_status(task_id)
    assert status["status"] == "completed", (status, handle.message, handle.result)
    assert not handle._thread.is_alive()
    assert handle.notified
    return handle.result


def _load_translated(project, target):
    if target == "eet":
        load_source(project)
        format_id = FormatId.XML_EET
    else:
        project.source.write_bytes(Path("tests/contracts/io/fixtures/xt-small.xml").read_bytes())
        result = _tool_parse_xt({"path": str(project.source)}, ExecutionContext(app_context=project.ctx))
        assert result.success, result.message
        format_id = FormatId.XML_XT
    _translate_active(project)
    return format_id


def _translate_active(project):
    entry = next(iter(project.ctx.collection))
    result = project.services.gui_project_commands.update_entry(
        entry.identity, project.request, translation=_TRANSLATION, stage=1
    )
    assert result.is_success, result.diagnostics
    states = project.services.project_projection.snapshot().to_dict()["values"]["entries"]
    slot = project.ctx.active_slot
    slot.collection = apply_variant_projection(slot.collection, states)
    project.ctx.add_slot(project.ctx.active_key, slot)
    assert not project.ctx.authoritative_projection_diverged()


def _load_localized_translated(project, localized):
    result = _tool_parse_esp({"path": str(localized.source)}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    assert next(iter(project.ctx.collection)).string_id == 1
    assert project.ctx.active_slot.source_snapshot is not None
    _translate_active(project)


def _load_referenced_plugin(project, *, save=True):
    source = write_plugin(project.root / "Source.esp", [(0x800, "Greeter", "Welcome")])
    project.ctx.project_source_references = ProjectSourceReferences(
        project.services.projects, project.services.project_lifecycle
    )
    context = ExecutionContext(app_context=project.ctx)
    result = _tool_parse_esp({"path": str(source)}, context)
    assert result.success, result.message
    _translate_active(project)
    active = project.services.project_lifecycle.active
    store = project.services.projects.store_for(active.project_ref.identity)
    if save is True:
        saved = project.services.gui_project_commands.save(project.request)
        assert saved.is_success, saved.diagnostics
        assert store.sources(native_fields=False)[0].reference_path == str(source)
    elif save == "metadata":
        binding = ParaTranzProjectBinding(42, "Linked project", "https://paratranz.cn")
        saved = project.services.project_remote_bindings.set_paratranz_binding(binding, project.request)
        assert saved.is_success, saved.diagnostics
    return source, store, ExecutionContext(app_context=project.ctx)


@pytest.mark.parametrize("save", [True, False, "metadata"])
def test_agent_uses_project_plugin_reference_only_as_input(project, manager, save):
    source, store, context = _load_referenced_plugin(project, save=save)
    original = source.read_bytes()
    request = SimpleNamespace(source_message_ids=(), execution_version_json=execution_version(context))
    apply_captured_scope(request, (), context)
    assert context.active_slot is None

    missing_output = _tool_write_back({"target": "esp"}, context)
    assert missing_output.error_code == "OUTPUT"
    assert manager.list_all() == []
    destination = project.root / "translated.esp"
    result = _wait_for_result(manager, _tool_write_back({"target": "esp", "path": str(destination)}, context))

    assert result["written_count"] == 1
    assert destination.is_file()
    assert source.read_bytes() == original
    assert bool(store.sources(native_fields=False)) is (save is True)


@pytest.mark.parametrize("wrong_content", [False, True])
@pytest.mark.parametrize("save", [True, False, "metadata"])
def test_agent_remembers_explicit_match_only_after_preflight(project, manager, wrong_content, save):
    source, store, context = _load_referenced_plugin(project, save=save)
    lifecycle = project.services.project_lifecycle
    relocated = project.root / "relocated"
    relocated.mkdir()
    replacement = source.rename(relocated / source.name)
    if wrong_content:
        write_plugin(source, [(0x800, "Greeter", "Different plugin content")])
    destination = project.root / "translated.esp"
    args = {"target": "esp", "path": str(destination)}

    missing = _tool_write_back(args, context)
    assert missing.error_code == "WRITE_PLUGIN_INPUT_REQUIRED"
    destination.write_bytes(b"existing target")
    blocked = _tool_write_back({**args, "input_plugin": str(replacement)}, context)
    assert blocked.error_code == "OVERWRITE_CONFIRMED"
    before = store.sources(native_fields=False) if save is True else lifecycle.active.source_additions
    assert before[0].reference_path == str(source)
    assert manager.list_all() == []

    _wait_for_result(manager, _tool_write_back({**args, "input_plugin": str(replacement), "overwrite": True}, context))
    after = store.sources(native_fields=False) if save is True else lifecycle.active.source_additions
    assert after[0].reference_path == str(replacement)
    second_output = project.root / "translated-again.esp"
    _wait_for_result(manager, _tool_write_back({**args, "path": str(second_output)}, context))
    assert second_output.is_file()
    if save is not True:
        assert not store.sources(native_fields=False)
        saved = project.services.gui_project_commands.save(project.request)
        assert saved.is_success, saved.diagnostics
        assert store.sources(native_fields=False)[0].reference_path == str(replacement)


def test_agent_rejects_saved_plugin_outside_grant_before_binding(project, manager, monkeypatch):
    source, store, _context = _load_referenced_plugin(project)
    output = project.root / "allowed-output"
    output.mkdir()
    context = ExecutionContext(
        app_context=project.ctx, request_context=replace(project.request, authorized_roots=(str(output),))
    )

    def unexpected(*_args, **_kwargs):
        pytest.fail("An unauthorized saved plugin must not reach the parser")

    monkeypatch.setattr("transbridge.smart_assistant.tools.write_back_preparation.bind_plugin_write_input", unexpected)
    result = _tool_write_back({"target": "esp", "path": str(output / source.name)}, context)

    assert result.error_code == "WRITE_PLUGIN_INPUT_REQUIRED"
    assert str(source) not in result.message
    assert store.sources(native_fields=False)[0].reference_path == str(source)
    assert not tuple(output.iterdir())
    assert manager.list_all() == []


def test_localized_request_preparation_keeps_companions_for_current_operation(localized):
    snapshot = replace(
        localized.parsed.source_snapshot,
        metadata=(
            *localized.parsed.source_snapshot.metadata,
            ("custom", {"tuple": (1, b"\x00"), "list": [b"\xff"], "literal_tag": {"$bytes": "business"}}),
        ),
    )
    entries = tuple(
        replace(entry, metadata=(*entry.metadata, ("binary", (b"\x00\xff", ["text"]))))
        for entry in localized.parsed.entries
    )
    prepared = prepare_parsed_source(str(localized.source), entries, snapshot, FormatId.PLUGIN_SSE)
    assert prepared.hydration.source_snapshot == snapshot
    assert [entry.entry_key for entry in prepared.hydration.entries] == [entry.identity for entry in entries]
    assert dict(prepared.hydration.entries[0].metadata)["binary"] == (b"\x00\xff", ["text"])
    companions = dict(prepared.hydration.source_snapshot.metadata)["localized_sources"]
    assert isinstance(companions, tuple) and len(companions) == 3
    assert all(isinstance(rows, tuple) and companion.content for companion, rows in companions)


def test_localized_source_save_retry_keeps_rows_without_plugin_or_companions(project, localized, monkeypatch):
    from transbridge.persistence.task_store.recovery import RecoveryView
    from transbridge.smart_assistant.tools.tool_translator import _tool_get_task_result, _tool_merge_task_result

    create = RecoveryView.create
    monkeypatch.setattr(RecoveryView, "create", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    context = ExecutionContext(app_context=project.ctx)
    result = _tool_parse_esp({"path": str(localized.source)}, context)
    assert result.partial and not result.success
    for path in localized.source.parent.rglob("*"):
        if path.is_file():
            path.unlink()
    monkeypatch.setattr(RecoveryView, "create", create)
    recovered = _tool_merge_task_result({"result_id": result.data["result_id"]}, context)
    assert not recovered.success and "重新" in recovered.message
    assert not project.ctx.project_sources
    viewed = _tool_get_task_result({"result_id": result.data["result_id"]}, context)
    assert viewed.success and [row["original"] for row in viewed.data["entries"]] == ["Original 1"]
    assert str(localized.source) not in repr(viewed.data)
    assert "input_snapshot" not in viewed.data


def _read_translation(path, format_id):
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(path)), RequestContext("verify"), format_id)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    return parsed.entries[0].translation


@pytest.mark.parametrize("target", ["eet", "xt"])
def test_agent_writes_new_xml_from_hydration_with_real_task_runtime(project, manager, target):
    format_id = _load_translated(project, target)
    destination = project.root / "translated.xml"
    # The captured snapshot is the source of truth; publishing must not reparse this path.
    project.source.unlink()

    started = _tool_write_back({"target": target, "path": str(destination)}, ExecutionContext(app_context=project.ctx))
    result = _wait_for_result(manager, started)

    assert result["written_count"] == 1
    assert result["artifacts"] == [str(destination)]
    assert _read_translation(destination, format_id) == _TRANSLATION
    assert not tuple(project.root.glob(".translated.xml.*.bak"))


@pytest.mark.parametrize("target", ["eet", "xt"])
def test_agent_default_overwrite_is_rejected_before_starting_a_task(project, manager, target):
    _load_translated(project, target)
    destination = project.root / "existing.xml"
    original = b"existing target must survive"
    destination.write_bytes(original)

    result = _tool_write_back({"target": target, "path": str(destination)}, ExecutionContext(app_context=project.ctx))

    assert not result.success
    assert result.error_code == "OVERWRITE_CONFIRMED"
    assert destination.read_bytes() == original
    assert manager.list_all() == []
    assert not tuple(project.root.glob(".existing.xml.*.bak"))


@pytest.mark.parametrize("target", ["eet", "xt"])
def test_agent_explicit_overwrite_keeps_original_backup_and_publishes_translation(project, manager, target):
    format_id = _load_translated(project, target)
    destination = project.root / "existing.xml"
    original = b"original target bytes"
    destination.write_bytes(original)

    started = _tool_write_back(
        {"target": target, "path": str(destination), "overwrite": True}, ExecutionContext(app_context=project.ctx)
    )
    _wait_for_result(manager, started)

    assert _read_translation(destination, format_id) == _TRANSLATION
    backups = tuple(project.root.glob(".existing.xml.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original


def test_agent_without_source_snapshot_fails_before_task_registration(project, manager):
    _load_translated(project, "eet")
    project.ctx.active_slot.source_snapshot = None
    destination = project.root / "translated.xml"

    result = _tool_write_back({"target": "eet", "path": str(destination)}, ExecutionContext(app_context=project.ctx))

    assert not result.success
    assert result.error_code == "SOURCE_SNAPSHOT_REQUIRED"
    assert not destination.exists()
    assert manager.list_all() == []


def test_relative_output_uses_the_same_authorized_base_as_the_guard(project, manager):
    from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard

    _load_translated(project, "eet")
    context = ExecutionContext(app_context=project.ctx, request_context=project.request)
    args = {"target": "eet", "path": "relative-output.xml"}
    guard = InputValidationGuard()
    assert guard.before_execute({"tool": "write_back", "args": args}, context).allowed
    assert not guard.before_execute({"tool": "parse_eet", "args": {"path": args["path"]}}, context).allowed

    started = _tool_write_back(args, context)
    assert started.success, started.message
    _wait_for_result(manager, started)
    destination = project.root / args["path"]
    assert Path(started.data["path"]) == destination
    assert destination.is_file()


def test_agent_rejects_format_mismatch_and_ungranted_output_root(project, manager):
    _load_translated(project, "eet")
    context = ExecutionContext(app_context=project.ctx)
    mismatch = _tool_write_back({"target": "xt", "path": str(project.root / "wrong.xml")}, context)
    outside = project.root.parent / "outside-authorized-directory.xml"
    forbidden = _tool_write_back({"target": "eet", "path": str(outside)}, context)
    ungranted = _tool_write_back(
        {"target": "eet", "path": str(project.root / "ungranted.xml")},
        ExecutionContext(app_context=project.ctx, request_context=replace(project.request, authorized_roots=())),
    )

    assert not mismatch.success and mismatch.error_code == "SOURCE_SNAPSHOT_REQUIRED"
    assert not forbidden.success
    assert not outside.exists()
    assert not ungranted.success and ungranted.error_code == "PATH_GRANT_REQUIRED"
    assert manager.list_all() == []


@pytest.mark.parametrize("target", ["esp", "strings"])
def test_agent_publishes_localized_bundle_without_precreating_strings_directory(project, localized, manager, target):
    _load_localized_translated(project, localized)
    destination = localized.target if target == "esp" else localized.output / localized.source.name
    args = {
        "target": target,
        "path": str(destination),
        "output_dir": str(localized.output),
        "input_plugin": str(localized.source),
    }
    assert localized.output.is_dir()
    assert not (localized.output / "Strings").exists()
    original_files = {path: path.read_bytes() for path in localized.source.parent.rglob("*") if path.is_file()}

    started = _tool_write_back(args, ExecutionContext(app_context=project.ctx))
    assert started.success, (started.error_code, started.message)
    result = _wait_for_result(manager, started)

    expected = {
        destination,
        *(
            localized.output / "Strings" / f"{destination.stem}_English.{suffix}"
            for suffix in ("strings", "dlstrings", "ilstrings")
        ),
    }
    assert {Path(path) for path in result["artifacts"]} == expected
    assert {path for path in localized.output.rglob("*") if path.is_file()} == expected
    reparsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(destination)), RequestContext("verify-bundle"), FormatId.PLUGIN_SSE)
    )
    assert reparsed.outcome is OperationOutcome.COMPLETED, reparsed.diagnostics
    assert reparsed.entries[0].original == _TRANSLATION
    assert reparsed.entries[0].string_id == 1
    for suffix in ("dlstrings", "ilstrings"):
        assert (localized.output / "Strings" / f"{destination.stem}_English.{suffix}").read_bytes() == original_files[
            localized.source.parent / "Strings" / f"localized_English.{suffix}"
        ]
    assert all(path.read_bytes() == content for path, content in original_files.items())


@pytest.mark.parametrize("missing_directory", [False, True])
def test_agent_localized_bundle_rejects_escape_without_creating_output_directories(
    project, localized, manager, missing_directory
):
    _load_localized_translated(project, localized)
    directory = localized.output / "not-created" / "nested" if missing_directory else localized.output
    context = ExecutionContext(
        app_context=project.ctx,
        request_context=replace(project.request, authorized_roots=(str(localized.source.parent),)),
    )

    result = _tool_write_back(
        {"target": "strings", "output_dir": str(directory), "input_plugin": str(localized.source)}, context
    )

    assert not result.success
    assert result.error_code in {"PATH_OUTSIDE_GRANT", "PATH_RESOLUTION_FAILED"}
    assert not tuple(localized.output.iterdir())
    assert manager.list_all() == []


@pytest.mark.parametrize("operation", ["write", "batch"])
def test_authoritative_gui_write_card_never_falls_back_to_legacy_writers(project, monkeypatch, operation):
    load_source(project)
    slot = project.ctx.active_slot
    slot.source_snapshot = None
    warnings = []

    def unexpected(*_args, **_kwargs):
        pytest.fail("An authoritative source must not reach legacy writers or save dialogs")

    monkeypatch.setattr("transbridge.writer.plugin_writer.PluginWriter", unexpected)
    monkeypatch.setattr("transbridge.writer.eet_xml_writer.EETWriter", unexpected)
    monkeypatch.setattr("transbridge.writer.xt_xml_writer.XTWriter", unexpected)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", unexpected)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))
    card = write_card_module.WriteCard(project.ctx, unexpected)
    card.bind_operation_plan_facade(SimpleNamespace(supports=lambda *_args, **_kwargs: False, begin_write=unexpected))
    before = project.services.project_lifecycle.active.variant.snapshot()
    try:
        if operation == "write":
            card.write()
        else:
            card.do_batch_write([slot])
        assert warnings
        assert project.services.project_lifecycle.active.variant.snapshot() == before
    finally:
        card.close()
        card.deleteLater()
        _APP.processEvents()
