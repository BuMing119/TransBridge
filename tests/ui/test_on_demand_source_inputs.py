from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QFileDialog, QLabel
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as shared_plugin_fixture
from tests.ui.test_project_source_hydration import _snapshot, _source
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.source_supplement_models import (
    SourceSupplementChoice,
    SourceSupplementPreview,
    SourceSupplementRow,
)
from transbridge.ui.source_hydration import prepare_project_slots
from transbridge.ui.source_input_state import SourceInputScope, missing_source_keys, original_known
from transbridge.ui.source_supplement_dialog import SourceSupplementDialog
from transbridge.ui.tools.ai_translator._window_actions import missing_original_source_keys
from transbridge.ui.tools.ai_translator.scope_presenter import TranslationScope
from transbridge.ui.tools.ai_translator.task_scope import SourceWorkbenchScope, TaskScope
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_model import TranslationTableModel
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL, COL_TRANSLATION

shared_plugin_window = shared_plugin_fixture


def test_saved_rows_keep_exact_translation_identity_and_known_original_without_snapshot():
    known = EntryKey(SourceNamespace("saved"), "same", "Saved original")
    unknown = EntryKey(SourceNamespace("saved"), "other")
    hidden = EntryKey(SourceNamespace("saved"), "deleted")
    snapshot = _snapshot([
        {"entry_key": known.to_dict(), "translation": "Known translation", "stage": 5, "revision": 7},
        {"entry_key": unknown.to_dict(), "translation": "Unknown translation", "stage": 1},
        {"entry_key": hidden.to_dict(), "translation": "Deleted translation", "stage": 1, "tombstone": True},
    ])
    slots = dict(prepare_project_slots((), snapshot))
    slot = slots["saved"]
    entries = tuple(slot.collection)
    assert slot.source_snapshot is None and {entry.identity for entry in entries} == {known, unknown}
    assert entries[0].original == "Saved original" and original_known(entries[0])
    assert entries[0].revision.value == 7 and entries[0].translation == "Known translation"
    assert not original_known(entries[1])
    model = TranslationTableModel()
    model.install(RenderSession(1, 1, entries), (0, 1), {}, {})
    assert model.index(0, COL_ORIGINAL).data() == "Saved original"
    assert model.index(1, COL_ORIGINAL).data() == "原文待补充"
    assert model.flags(model.index(1, COL_TRANSLATION)) & Qt.ItemFlag.ItemIsEditable
    assert len(snapshot.values["entries"]) == 3
    known_slot = replace(slot, collection=type(slot.collection)((entries[0],)))
    context = SimpleNamespace(slots={"saved": known_slot})
    assert missing_source_keys(context, "originals") == ()
    assert missing_source_keys(context, "template") == ("saved",)


def test_mixed_slots_only_repair_the_selected_missing_source():
    source = _source("ready.esp", "ready")
    missing = EntryKey(SourceNamespace("missing"), "bare")
    snapshot = _snapshot([{"entry_key": missing.to_dict(), "translation": "Retained"}])
    context = SimpleNamespace(slots=dict(prepare_project_slots((source,), snapshot)))
    assert missing_source_keys(context, "template", ("ready",)) == ()
    assert missing_source_keys(context, "originals", ("ready",)) == ()
    assert missing_source_keys(context, "template", ("missing",)) == ("missing",)
    scope = SourceInputScope(context, ("ready",))
    assert tuple(scope.slots) == ("ready",)


def test_ai_original_requirement_uses_selected_entries_not_other_unknown_rows():
    known = EntryKey(SourceNamespace("saved"), "known", "Actual saved original")
    unknown = EntryKey(SourceNamespace("saved"), "unknown")
    slots = dict(
        prepare_project_slots(
            (),
            _snapshot([
                {"entry_key": key.to_dict(), "translation": "Saved translation", "stage": 1} for key in (known, unknown)
            ]),
        )
    )
    slot = slots["saved"]
    context = SimpleNamespace(slots=slots, active_slot=slot, entry_labels={})
    window = SimpleNamespace(
        _ctx=context,
        _view=SimpleNamespace(sources_panel=SimpleNamespace(selected_slots=lambda: (slot,))),
        _task_scope=TaskScope(context, SourceWorkbenchScope(), lambda _entry: ""),
        _scope_presenter=SimpleNamespace(
            state=TranslationScope(preset="selection", selected_entry_ids=frozenset({next(iter(slot.collection)).id}))
        ),
    )
    assert missing_original_source_keys(window) == ()
    window._scope_presenter.state = TranslationScope()
    assert missing_original_source_keys(window) == ("saved",)


def test_correspondence_preview_uses_one_selector_for_large_inputs(shared_plugin_window):
    old = EntryKey(SourceNamespace("legacy:v1"), "old")
    choices = tuple(
        SourceSupplementChoice(EntryKey(SourceNamespace("new"), str(index)), str(index), f"Original {index}")
        for index in range(5000)
    )
    preview = SourceSupplementPreview(
        "token",
        "chosen.esp",
        "chosen.esp",
        3,
        0,
        1,
        5000,
        (SourceSupplementRow(old, "Saved translation", None),),
        choices,
    )
    dialog = SourceSupplementDialog(preview)
    try:
        assert len(dialog.findChildren(QComboBox)) == 1
        assert dialog.choices.choices is choices
        assert any(
            "3 个翻译版本" in label.text() and "待手动对应 1 条" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        dialog.selector.setCurrentIndex(5000)
        assert dialog.mappings() == {old: choices[-1].key}
        dialog.selector.setCurrentIndex(0)
        assert dialog.mappings() == {old: None}
    finally:
        dialog.close()


@pytest.mark.parametrize("selection", ["cancel", "matching", "wrong", "unreadable"])
def test_plugin_write_binds_only_the_current_operation(shared_plugin_window, monkeypatch, selection):
    case = shared_plugin_window
    context = case.window.context
    facade = case.window.operation_plan_facade
    before = case.services.project_lifecycle.active.variant.snapshot()
    source_id = context.active_key
    original_slot = context.active_slot
    expected = case.plugin.read_bytes()
    assert original_slot.source_snapshot.content is None
    selected_dir = case.root / "selected"
    selected_dir.mkdir()
    selected = case.plugin.rename(selected_dir / case.plugin.name)
    if selection == "wrong":
        write_plugin(selected, [(0x900, "Other", "Other original")])
    if selection == "unreadable":
        resolve = Path.resolve

        def resolve_selected(path, *args, **kwargs):
            if path == selected:
                raise PermissionError(path)
            return resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve_selected)
    prompts, resumed, messages = [], [], []
    monkeypatch.setattr(case.window, "show_message", lambda text, **_: messages.append(text))
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda _host, title, *_: (prompts.append(title) or ("" if selection == "cancel" else str(selected)), ""),
    )
    monkeypatch.setattr(facade._coordinator, "begin_write", lambda scope, **_: resumed.append(scope))

    facade.begin_write(context, output_format=FormatId.PLUGIN_SSE.value)
    _until(lambda: case.window.foreground_worker is None)

    assert len(prompts) == 1
    assert context.active_slot is original_slot and original_slot.source_snapshot.content is None
    assert case.services.project_lifecycle.active.variant.snapshot() == before
    if selection == "matching":
        (scope,) = resumed
        assert tuple(scope.slots) == (source_id,)
        assert scope.slots[source_id].source_snapshot.content == expected
    else:
        assert not resumed
        if selection in {"wrong", "unreadable"}:
            assert messages
            if selection == "unreadable":
                assert any("无权读取工程来源文件" in message for message in messages)
        else:
            assert not messages


@pytest.mark.parametrize("shared_plugin_window", ["multiple"], indirect=True)
@pytest.mark.parametrize("cancel_second", [False, True])
def test_batch_plugin_inputs_cover_both_sources_or_cancel_without_binding(
    shared_plugin_window, monkeypatch, cancel_second
):
    case = shared_plugin_window
    context = case.window.context
    facade = case.window.operation_plan_facade
    sources = tuple(context.slots)
    assert len(sources) == 2
    paths = {path.name: path for path in (case.plugin, case.other_plugin)}
    selection = iter(paths[slot.label] for slot in context.slots.values())
    chosen, resumed = [], []
    before = case.services.project_lifecycle.active.variant.snapshot()

    def choose(*_):
        path = next(selection)
        chosen.append(path)
        return ("" if cancel_second and len(chosen) == 2 else str(path)), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    monkeypatch.setattr(facade._coordinator, "begin_write", lambda scope, **options: resumed.append((scope, options)))

    facade.begin_write(context, batch=True, output_format=FormatId.PLUGIN_SSE.value)
    _until(lambda: case.window.foreground_worker is None)

    assert set(chosen) == set(paths.values())
    assert tuple(context.slots) == sources
    assert all(slot.source_snapshot.content is None for slot in context.slots.values())
    assert case.services.project_lifecycle.active.variant.snapshot() == before
    if cancel_second:
        assert not resumed
    else:
        ((scope, options),) = resumed
        assert options["batch"] is True
        assert tuple(scope.slots) == sources
        assert [slot.source_snapshot.content for slot in scope.slots.values()] == [path.read_bytes() for path in chosen]


@pytest.mark.parametrize("output,suffix", [("json.paratranz", ".json"), ("json.dsd", ".json"), ("sst.ssu9", ".sst")])
def test_generic_export_offers_output_format_without_the_original_plugin(
    shared_plugin_window, monkeypatch, output, suffix
):
    case = shared_plugin_window
    facade = case.window.operation_plan_facade
    initial_windows = facade.active_plan_count
    case.plugin.unlink()
    target = case.root / f"out{suffix}"
    prompts = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda _host, title, *_: (prompts.append(title) or "", ""))

    try:
        case.window.operation_coordinator.write()
        assert prompts == [], "Format selection must precede a plugin picker"
        assert facade.active_plan_count == initial_windows + 1
        (dialog,) = facade._coordinator._owned_windows.values()
        formats = dialog._field_edits["output_format"]
        index = formats.findData(output)
        assert index >= 0
        formats.setCurrentIndex(index)
        dialog._field_edits["target_path"].setText(str(target))
        dialog._confirm_button.click()
        _until(lambda: dialog._checks.property("tbStatusId") == "completed")
        assert target.stat().st_size > 0
        assert not prompts
    finally:
        for dialog in tuple(facade._coordinator._owned_windows.values()):
            dialog.close()


def test_generic_native_export_prompts_only_on_submit_and_can_retry_after_cancel(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    target = case.root / "out.esp"
    original = case.plugin.read_bytes()
    selections = iter(("", str(case.plugin)))
    prompts = []

    def choose(_host, title, *_args):
        prompts.append(title)
        return next(selections), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    facade = case.window.operation_plan_facade
    case.window.operation_coordinator.write()
    (dialog,) = facade._coordinator._owned_windows.values()
    try:
        assert not prompts
        dialog._field_edits["target_path"].setText(str(target))
        dialog._confirm_button.click()
        assert len(prompts) == 1
        assert dialog.isVisible() and dialog._confirm_button.isEnabled()
        assert not target.exists()

        dialog._confirm_button.click()
        _until(lambda: dialog._checks.property("tbStatusId") == "completed")
        assert target.stat().st_size > 0
        assert len(prompts) == 2
        assert case.plugin.read_bytes() == original
        assert case.window.context.active_slot.source_snapshot.content is None
    finally:
        dialog.close()


@pytest.mark.parametrize("shared_plugin_window", ["multiple"], indirect=True)
def test_generic_batch_json_exports_each_source_without_plugin_inputs(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    case.plugin.unlink()
    case.other_plugin.unlink()
    output = case.root / "exports"
    output.mkdir()
    prompts = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (prompts.append(True) or "", ""))

    facade = case.window.operation_plan_facade
    case.window.operation_coordinator.batch_write()
    (dialog,) = facade._coordinator._owned_windows.values()
    try:
        formats = dialog._field_edits["output_format"]
        index = formats.findData("json.paratranz")
        assert index >= 0
        formats.setCurrentIndex(index)
        dialog._field_edits["output_directory"].setText(str(output))
        dialog._confirm_button.click()
        _until(lambda: dialog._checks.property("tbStatusId") == "completed")
        assert {path.name for path in output.glob("*.json")} == {"A.json", "B.json"}
        assert not prompts
    finally:
        dialog.close()
