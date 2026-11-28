"""Compare the same files through the desktop workers and the admitted assistant tools."""

from dataclasses import replace

import pytest

from tests.contracts.io.test_open_translation_file import FORMATS, source_file
from tests.smart_assistant.tools.test_source_import_authority import file_project, project as project_fixture
from tests.ui.test_open_file_workflow import host as host_fixture, start, wait
from transbridge.application.tasks.controls import CancellationToken
from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard
from transbridge.smart_assistant.tools import _source_import, tool_parser
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.coordinators.migration_coordinator import run_migration
from transbridge.ui.workbench.translation_import_dialog import TranslationImportConfig

project = project_fixture
host = host_fixture
TOOLS = {
    "plugin.sse": tool_parser._tool_parse_esp,
    "binary.eet": tool_parser._tool_parse_eet,
    "xml.eet": tool_parser._tool_parse_eet,
    "xml.xt": tool_parser._tool_parse_xt,
    "sst.ssu8": tool_parser._tool_parse_sst,
    "sst.ssu9": tool_parser._tool_parse_sst,
    "json.dsd": tool_parser._tool_import_json,
    "json.paratranz": tool_parser._tool_import_json,
    "json.transbridge": tool_parser._tool_import_json,
}


@pytest.fixture
def assistant(project, monkeypatch):
    root = project.root / "assistant"
    root.mkdir()
    with file_project(root, monkeypatch) as other:
        request = replace(other.request, authorized_roots=(str(project.root),))
        yield other, ExecutionContext(app_context=other.ctx, request_context=request)


def _rows(ctx):
    return [(e.identity, e.original, e.translation, e.stage, e.external_refs, e.metadata) for e in ctx.collection]


@pytest.mark.parametrize("kind", FORMATS)
def test_desktop_and_assistant_register_identical_source(project, host, assistant, kind):
    other, execution = assistant
    path = source_file(project.root, kind)
    start(project, host, [path])
    wait(project, lambda: not host.opener._running)
    assert project.ctx.active_slot, host.messages
    result = TOOLS[kind]({"path": str(path)}, execution)
    assert result.success and result.data["entry_count"] == len(project.ctx.collection), result.message
    assert "result_id" not in result.data
    assert other.ctx.active_slot.format_id == project.ctx.active_slot.format_id
    assert other.ctx.active_slot.source_snapshot == project.ctx.active_slot.source_snapshot
    assert _rows(other.ctx) == _rows(project.ctx)


@pytest.mark.parametrize("kind", (*FORMATS, "json.transbridge"))
def test_desktop_and_assistant_fill_replace_and_reimport_identical_file(project, host, assistant, kind):
    from tests.smart_assistant.tools.test_source_import_authority import write_json

    other, execution = assistant
    path = source_file(project.root, "xml.eet" if kind == "json.transbridge" else kind)
    plugin = project.root / "Test.esp"
    start(project, host, [plugin])
    wait(project, lambda: not host.opener._running)
    assert tool_parser._tool_parse_esp({"path": str(plugin)}, execution).success
    entry = next(iter(project.ctx.collection))
    if kind == "json.transbridge":
        path = write_json(project, "donor.json", [{**entry.to_dict(), "translation": "你好", "stage": 1}])
    for case in (project, other):
        assert case.ctx.project_commands.replace_entry_states(
            {entry.identity: ("已有译文", 1)}, case.request
        ).is_success

    for overwrite, expected, changed in ((False, "已有译文", 0), (True, "你好", 1), (True, "你好", 0)):
        host.messages.clear()
        run_migration(
            host,
            project.ctx.active_slot,
            TranslationImportConfig(
                import_source_path=str(path),
                replace_existing=overwrite,
            ),
        )
        wait(project, lambda: bool(host.messages))
        result = TOOLS[kind](
            {"path": str(path), "action": "append", "replace_existing": overwrite},
            ExecutionContext(app_context=other.ctx, request_context=execution.request_context),
        )
        assert result.success and result.data["added_count"] == changed, result.message
        assert project.ctx.collection.get(entry.identity).translation == expected, host.messages
        assert _rows(other.ctx) == _rows(project.ctx)


@pytest.mark.parametrize("action", ["create_slot", "append"])
def test_cancel_after_file_preparation_prevents_commit(project, monkeypatch, action):
    path = source_file(project.root, "xml.eet")
    if action == "append":
        assert tool_parser._tool_parse_esp({"path": str(project.root / "Test.esp")}, project.ctx).success
    ctx = ExecutionContext(app_context=project.ctx)
    token = CancellationToken()
    ctx.cancellation_token = token
    name = "prepare_open_file" if action == "create_slot" else "prepare_translation_import"
    prepare = getattr(_source_import, name)

    def cancel_after_prepare(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        token._cancel("用户已取消")
        return prepared

    monkeypatch.setattr(_source_import, name, cancel_after_prepare)
    before = project.services.project_lifecycle.active.variant.snapshot()
    sources = tuple(project.ctx.project_sources)
    result = tool_parser._tool_parse_eet({"path": str(path), "action": action}, ctx)
    assert not result.success and "取消" in result.message
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert tuple(project.ctx.project_sources) == sources


def test_file_tool_reads_relative_path_against_the_admitted_root(project):
    ctx = ExecutionContext(app_context=project.ctx, request_context=project.request)
    args = {"path": project.source.name}
    guard = InputValidationGuard()
    assert guard.before_execute({"tool": "parse_eet", "args": args}, ctx).allowed
    result = tool_parser._tool_parse_eet(args, ctx)
    assert result.success and result.data["activated"], result.message
    restricted = project.root / "restricted"
    restricted.mkdir()
    ctx = ExecutionContext(
        app_context=project.ctx, request_context=replace(project.request, authorized_roots=(str(restricted),))
    )
    args = {"path": str(project.source)}
    assert not guard.before_execute({"tool": "parse_eet", "args": args}, ctx).allowed
    result = tool_parser._tool_parse_eet(args, ctx)
    assert not result.success and "PATH_OUTSIDE_GRANT" in result.message


def test_same_key_different_originals_receive_only_matching_donor(project, host, assistant):
    from tests.plugin_fixtures import write_plugin
    from tests.smart_assistant.tools.test_source_import_authority import write_json

    other, execution = assistant
    plugin = write_plugin(project.root / "Collisions.esp", [(0x800, "Same", "First"), (0x800, "Same", "Second")])
    start(project, host, [plugin])
    wait(project, lambda: not host.opener._running)
    assert tool_parser._tool_parse_esp({"path": str(plugin)}, execution).success
    first, second = project.ctx.collection
    assert first.key == second.key
    for original, expected in ((second.original, 1), ("Missing", 0)):
        path = write_json(project, "target.json", [{"key": first.key, "original": original, "translation": "Matched"}])
        host.messages.clear()
        run_migration(host, project.ctx.active_slot, TranslationImportConfig(import_source_path=path))
        wait(project, lambda: bool(host.messages))
        result = tool_parser._tool_import_json({"path": path, "action": "append"}, execution)
        assert result.success and result.data["added_count"] == expected, result.message
        assert _rows(other.ctx) == _rows(project.ctx)
    assert not other.ctx.collection.get(first.identity).translation
    assert other.ctx.collection.get(second.identity).translation == "Matched"


@pytest.mark.parametrize("action", ["create_slot", "append"])
def test_revision_changed_after_prepare_rejects_action(project, monkeypatch, action):
    path = source_file(project.root, "xml.eet")
    assert tool_parser._tool_parse_esp({"path": str(project.root / "Test.esp")}, project.ctx).success
    ctx = ExecutionContext(app_context=project.ctx)
    key = next(iter(project.ctx.collection)).identity
    name = "prepare_open_file" if action == "create_slot" else "prepare_translation_import"
    prepare = getattr(_source_import, name)

    def concurrent_edit(*args, **kwargs):
        draft = prepare(*args, **kwargs)
        assert project.ctx.project_commands.replace_entry_states({key: ("最近编辑", 1)}, project.request).is_success
        return draft

    monkeypatch.setattr(_source_import, name, concurrent_edit)
    result = tool_parser._tool_parse_eet({"path": str(path), "action": action}, ctx)
    assert not result.success and "变化" in result.message
    assert len(project.ctx.project_sources) == 1
    assert project.ctx.collection.get(key).translation == "最近编辑"


def test_source_registration_reports_committed_when_cancelled_during_failed_view_install(project, monkeypatch):
    ctx = ExecutionContext(app_context=project.ctx)
    token = CancellationToken()
    ctx.cancellation_token = token
    revision = project.services.project_lifecycle.active.project.envelope.revision

    def reject_view(*args, **kwargs):
        token._cancel("用户已取消")
        return False

    monkeypatch.setattr(project.ctx, "install_working_sources", reject_view)
    result = tool_parser._tool_parse_eet({"path": str(project.source)}, ctx)
    assert result.success and not result.data["activated"] and result.warnings
    assert project.ctx.project_view_failed
    assert project.services.project_lifecycle.active.project.envelope.revision == revision + 1
    assert len(project.ctx.project_sources) == 1
    assert project.ctx.project_commands.save(project.request).is_success


@pytest.mark.parametrize("qualified", [False, True])
def test_import_saved_only_project_uses_retained_original_or_desktop_repair(tmp_path, qualified):
    import json
    from types import SimpleNamespace

    from tests.application.projects.test_known_original_consumers import _reopen, _saved
    from transbridge.ui.saved_translation_slots import saved_translation_slots
    from transbridge.ui.source_input_state import missing_source_keys

    source, project_path, request, _ = _saved(tmp_path, qualified=qualified)
    source.unlink()
    request = replace(request, authorized_roots=(str(tmp_path),))
    with _reopen(tmp_path, project_path, request) as services:
        active = services.project_lifecycle.active
        states = active.variant.snapshot().entries
        rows = [
            {"entry_key": state.entry_key.to_dict(), "translation": state.translation, "stage": state.stage.value}
            for state in states
        ]
        slots = dict(saved_translation_slots(SimpleNamespace(values={"entries": rows}), ()))
        key, slot = next(iter(slots.items()))
        app = SimpleNamespace(
            slots=slots,
            active_slot=slot,
            active_key=key,
            collection=slot.collection,
            active_version_identity=(active.project_ref.identity.value, active.formal_variant_ref.identity.value),
            active_project_id=active.project_ref.identity.value,
            active_variant_id=active.formal_variant_ref.identity.value,
            project_revision=active.project.envelope.revision,
            variant_revision=active.variant.revision,
            project_opening=False,
            project_view_failed=False,
            project_commands=services.gui_project_commands,
            runtime_context=request,
        )
        assert missing_source_keys(app, "originals") == (() if qualified else (key,))
        donor = tmp_path / "donor.json"
        donor.write_text(
            json.dumps([{"key": states[0].entry_key.local_key, "original": "First", "translation": "New"}])
        )
        result = tool_parser._tool_import_json({"action": "append", "path": str(donor), "replace_existing": True}, app)
        if qualified:
            assert result.success and result.data["added_count"] == 1, result.message
            assert {item.translation for item in active.variant.snapshot().entries} == {"Saved", "New"}
        else:
            assert result.error_code == "SOURCE_INPUT_REQUIRED", result.message
            assert result.data["source_input_repair"]["missing_source_keys"] == [key]
            assert active.variant.snapshot().entries == states


def test_registered_source_updates_dictionary_scope_for_the_next_tool(project, monkeypatch):
    from tests.smart_assistant.tools.test_source_import_authority import load_source
    from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary

    load_source(project)
    project.ctx.eet_path = str(project.source)
    execution = ExecutionContext(app_context=project.ctx)
    assert execution._target_dictionary_source_path == str(project.source)
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: project.root / "terms")
    path = source_file(project.root, "plugin.sse")
    result = tool_parser._tool_parse_esp({"path": str(path)}, execution)
    assert result.success and result.data["activated"]
    entry = next(iter(project.ctx.collection))
    DictionaryLibrary().save([replace(entry, translation="Applied after registration", stage=1)], "Terms")
    result = _tool_apply_dictionary({}, execution)
    assert result.success and result.data["applied"] == 1, result.message
    assert project.ctx.collection.get(entry.identity).translation == "Applied after registration"


@pytest.mark.parametrize("auto_apply", [True, False])
def test_open_plugin_uses_desktop_empty_original_choice_and_dictionary(
    project, host, assistant, monkeypatch, auto_apply
):
    from tests.plugin_fixtures import write_plugin
    from transbridge.application.io.open_translation_file import apply_open_file_dictionary
    from transbridge.config.dictionary_preferences import DictionaryPreferences
    from transbridge.config.repository import ConfigRepository
    from transbridge.parser.plugin_parser import PluginParser
    from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary

    other, execution = assistant
    config = project.root / "preferences.ini"
    preferences = DictionaryPreferences(ConfigRepository(config, legacy_path=config))
    preferences.set_apply_on_plugin_open(auto_apply)
    project.ctx.dictionary_preferences = other.ctx.dictionary_preferences = preferences
    path = write_plugin(project.root / "empty.esp", [(0x800, "Empty", ""), (0x801, "Named", "Greeting")])
    entry = next(item for item in PluginParser().parse_plugin(path) if item.original == "Greeting")
    monkeypatch.setattr("transbridge.translation_memory.library.default_library_dir", lambda: project.root / "terms")
    monkeypatch.setattr(
        "transbridge.ui.coordinators.parse_coordinator._apply_dictionary_to_collection", apply_open_file_dictionary
    )
    DictionaryLibrary().save([replace(entry, translation="From dictionary", stage=1)], "Terms")
    start(project, host, [path], skip_empty=False)
    wait(project, lambda: not host.opener._running)
    result = tool_parser._tool_parse_esp({"path": str(path), "skip_empty": False}, execution)
    assert result.success and result.data["entry_count"] == 2, result.message
    assert _rows(other.ctx) == _rows(project.ctx)
    assert [item.translation for item in project.ctx.collection if item.original] == [
        "From dictionary" if auto_apply else ""
    ]
    if not auto_apply:
        assert _tool_apply_dictionary({}, execution).data["applied"] == 1
        assert [item.translation for item in other.ctx.collection if item.original] == ["From dictionary"]


@pytest.mark.parametrize("admission", ["allowed", "refused", "receipt_failed", "cancelled_before_commit"])
def test_registration_keeps_disk_work_on_the_worker_and_view_delivery_on_qt(project, monkeypatch, admission):
    from concurrent.futures import ThreadPoolExecutor
    from threading import get_ident
    from types import SimpleNamespace

    from transbridge.ui.coordinators.open_file_coordinator import OpenedSourceView

    ctx = ExecutionContext(app_context=project.ctx)
    ui_thread = get_ident()
    events = []
    gate_active = False
    cancellation = CancellationToken()
    ctx.cancellation_token = cancellation

    def watch(obj, method):
        original = getattr(obj, method)

        def wrapped(*args, **kwargs):
            events.append((method, get_ident(), gate_active))
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, method, wrapped)

    for obj, method in (
        (project.ctx, "begin_project_open"),
        (project.ctx, "finish_project_open"),
        (project.ctx, "install_working_sources"),
        (project.ctx.project_commands, "add_prepared_source"),
        (OpenedSourceView, "prepare_slot"),
        (_source_import.ProjectToolTarget, "acknowledge_projection"),
    ):
        watch(obj, method)

    def gate_commit(effect_id, mutation):
        nonlocal gate_active
        assert effect_id == "register" and get_ident() != ui_thread
        if admission == "refused":
            return SimpleNamespace(accepted=False, reason="请求已停止")
        if admission == "cancelled_before_commit":
            cancellation._cancel("提交前取消")
        gate_active = True
        try:
            mutation()
        finally:
            gate_active = False
        if admission == "receipt_failed":
            raise OSError("receipt disk full")
        return SimpleNamespace(accepted=True)

    ctx.assistant_gate = SimpleNamespace(commit=gate_commit)
    ctx.assistant_effect_id = "register"
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(tool_parser._tool_parse_eet, {"path": str(project.source)}, ctx)
        wait(project, future.done)
        result = future.result()

    observed = [method for method, _, _ in events]
    assert observed.count("begin_project_open") == observed.count("finish_project_open") == 1
    assert not project.ctx.project_opening
    if admission in {"refused", "cancelled_before_commit"}:
        assert not result.success and not project.ctx.project_sources
        assert observed == ["begin_project_open", "finish_project_open"]
    else:
        assert result.success and result.data["activated"], result.message
        assert len(project.ctx.project_sources) == 1
        assert observed == [
            "begin_project_open",
            "add_prepared_source",
            "prepare_slot",
            "install_working_sources",
            "acknowledge_projection",
            "finish_project_open",
        ]
        if admission == "receipt_failed":
            assert any("回执" in warning for warning in result.warnings)
    for method, thread, gated in events:
        assert (thread != ui_thread) == (method in {"add_prepared_source", "prepare_slot"})
        assert gated == (method == "add_prepared_source")
