from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as _shared_plugin_window
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase

shared_plugin_window = _shared_plugin_window


def _drop_choice(case, path, choice, *, before_choice=None):
    controller = case.window._intent_composition._drop_controller
    observations = []
    timer = QTimer(case.window)
    timer.setSingleShot(True)

    def choose():
        dialog = controller.plugin_dialog
        if dialog is None:
            return
        observations.append((dialog.isVisible(), dialog.add_button.isEnabled(), dialog.notice.text()))
        if before_choice is not None:
            before_choice()
        if choice == "add" and dialog.add_button.isEnabled():
            dialog.add_button.click()
        elif choice == "new":
            dialog.new_button.click()
        else:
            dialog.reject()

    timer.timeout.connect(choose)
    timer.start(0)
    try:
        controller.binding.inspect_paths((str(path),))
    finally:
        timer.stop()
    assert len(observations) == 1
    assert observations[0][0]
    return observations[0]


def _dirty(case):
    context = case.window.context
    entry = next(iter(context.collection))
    result = context.update_projected_entry(entry.identity, translation="Unsaved A", stage=1)
    assert result.is_success, result.diagnostics
    context.collection = TranslationEntryCollection((replace(entry, translation="Unsaved A", stage=1),))
    assert context.dirty
    assert not context.authoritative_projection_diverged()


@pytest.mark.parametrize("shared_plugin_window", [False], indirect=True)
def test_drop_without_project_opens_prefilled_draft_without_creating_records(shared_plugin_window):
    case = shared_plugin_window
    controller = case.window._intent_composition._drop_controller

    controller.binding.inspect_paths((str(case.plugin),))

    assert controller.plugin_dialog is None
    assert controller.review_dialog is None
    draft = case.window.start_center_controller.guided_project.state
    assert draft.phase is GuidedDraftPhase.EDITING
    assert draft.project_name == "A"
    assert draft.source_path == str(case.plugin)
    assert case.window.start_center._pages.currentWidget() is case.window.start_center._draft_page
    assert case.services.project_lifecycle.active is None
    assert not (case.root / "data/project-catalog.json").exists()


def test_add_plugin_keeps_unsaved_entries_and_survives_save_and_reopen(shared_plugin_window):
    case = shared_plugin_window
    _dirty(case)
    original_identity = case.window.context.active_version_identity
    original_key = case.window.context.active_key
    other = write_plugin(case.root / "B.esp", [(0x900, "OtherNPC", "Other original")])
    original_bytes = case.plugin.read_bytes(), other.read_bytes()

    _, enabled, _ = _drop_choice(case, other, "add")
    assert enabled
    _until(lambda: case.window.foreground_worker is None)

    context = case.window.context
    assert context.active_version_identity == original_identity
    expected_keys = {item["source_id"] for item in context.project_sources}
    assert set(context.slots) == expected_keys
    assert context.active_key != original_key
    assert next(iter(context.slots[original_key].collection)).translation == "Unsaved A"
    assert context.dirty
    assert not context.project_opening and not context.project_view_failed
    assert not context.authoritative_projection_diverged()
    assert case.services.gui_project_commands.save(case.request).is_success
    path = case.services.projects.path_for(case.services.project_lifecycle.active.project_ref)
    case.window.project_coordinator.open_project_path(path)
    _until(lambda: case.window.project_open_worker is None)
    assert set(context.slots) == expected_keys
    assert next(iter(context.slots[original_key].collection)).translation == "Unsaved A"
    assert (case.plugin.read_bytes(), other.read_bytes()) == original_bytes


@pytest.mark.parametrize("same_content", [False, True])
def test_duplicate_content_is_checked_by_application_without_a_reference_path_gate(
    shared_plugin_window, monkeypatch, same_content
):
    case = shared_plugin_window
    path = case.plugin
    if same_content:
        path = case.root / "Copy.esp"
        path.write_bytes(case.plugin.read_bytes())
    old_slot = case.window.context.active_slot
    before = case.services.project_lifecycle.active.variant.snapshot()
    messages = []
    monkeypatch.setattr(case.window, "show_message", lambda text, **_: messages.append(text))

    _, enabled, _message = _drop_choice(case, path, "add")
    _until(lambda: case.window.foreground_worker is None)

    assert enabled
    assert any(
        code in text for text in messages for code in ("PROJECT_SOURCE_IDENTITY_DUPLICATE", "PROJECT_SOURCE_DUPLICATE")
    )
    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert case.window.context.active_slot is old_slot
    assert not case.window.context.project_view_failed
    assert not case.window.context.project_opening


def test_changed_content_at_existing_reference_can_be_added_as_an_independent_source(shared_plugin_window):
    case = shared_plugin_window
    context = case.window.context
    original_key = context.active_key
    original_slot = context.active_slot
    original_content = original_slot.source_snapshot.content
    write_plugin(case.plugin, [(0x900, "OtherNPC", "Replacement original")])

    _, enabled, _ = _drop_choice(case, case.plugin, "add")
    assert enabled
    _until(lambda: case.window.foreground_worker is None)

    assert len(context.slots) == 2
    assert context.active_key != original_key
    assert context.slots[original_key].source_snapshot.content == original_content
    assert context.active_slot.source_snapshot.content == case.plugin.read_bytes()


@pytest.mark.parametrize("decision", [QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel])
def test_new_project_choice_saves_or_cancels_unsaved_work(shared_plugin_window, monkeypatch, decision):
    case = shared_plugin_window
    _dirty(case)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: decision)

    _drop_choice(case, case.plugin, "new")
    _until(lambda: case.window.save_worker is None)

    assert case.window.context.active_project_id == case.original_project_id
    assert next(iter(case.window.context.collection)).translation == "Unsaved A"
    if decision == QMessageBox.StandardButton.Save:
        assert not case.window.context.dirty
        assert "Unsaved A" in Path(case.original_variant_path).read_text(encoding="utf-8")
        assert case.window.start_center_controller.guided_project.state.source_path == str(case.plugin)
    else:
        assert case.window.context.dirty
        assert case.window.mode_tabs.currentIndex() == 0
        assert case.window.start_center_controller.guided_project.state.source_path is None


def test_save_failure_does_not_open_a_new_draft(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    _dirty(case)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(case.window, "save_current_project_async", lambda *, on_finished: on_finished(False))

    _drop_choice(case, case.plugin, "new")

    assert case.window.context.dirty
    assert case.window.mode_tabs.currentIndex() == 0
    assert case.window.start_center_controller.guided_project.state.source_path is None


def test_switching_variant_during_choice_rejects_the_old_target(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    other = write_plugin(case.root / "B.esp", [(0x900, "OtherNPC", "Other original")])
    messages, changed = [], []
    monkeypatch.setattr(case.window, "show_message", lambda text, **_: messages.append(text))

    _drop_choice(
        case,
        other,
        "add",
        before_choice=lambda: changed.append(case.services.gui_project_commands.create_variant("Other", case.request)),
    )

    assert changed[0].is_success, changed[0].diagnostics
    assert any("目标项目已变化" in text for text in messages)
    assert case.window.foreground_worker is None
    assert len(case.services.project_lifecycle.active.project.envelope.data["sources"]) == 1


def test_view_install_failure_is_explicit_and_does_not_leave_partial_content_editable(
    shared_plugin_window, monkeypatch
):
    case = shared_plugin_window
    _dirty(case)
    other = write_plugin(case.root / "B.esp", [(0x900, "OtherNPC", "Other original")])
    old_slot = case.window.context.active_slot
    old_key = case.window.context.active_key
    messages = []
    monkeypatch.setattr(case.window, "show_message", lambda text, **_: messages.append(text))

    def fail(*_):
        raise RuntimeError("injected hydration failure")

    with monkeypatch.context() as failures:
        failures.setattr("transbridge.ui.coordinators.source_add_operation.prepare_project_slots", fail)
        _drop_choice(case, other, "add")
        _until(lambda: case.window.foreground_worker is None)

    assert case.window.context.project_view_failed
    assert not case.window.context.project_opening
    assert case.window.context.active_slot is old_slot
    assert len(case.services.project_lifecycle.active.project.envelope.data["sources"]) == 2
    assert any("injected hydration failure" in text and "重新打开工程" in text for text in messages)
    entry = next(iter(old_slot.collection))
    rejected = case.window.context.update_projected_entry(entry.identity, translation="Must not be applied")
    assert not rejected.is_success and rejected.diagnostics[0].code == "PROJECT_VIEW_UNAVAILABLE"

    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(str(args[2])))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: messages.append(str(args[2])))
    path = case.services.projects.path_for(case.services.project_lifecycle.active.project_ref)
    case.window.project_coordinator.open_project_path(path)
    _until(lambda: case.window.project_open_worker is None)
    assert not case.window.context.project_view_failed, messages
    assert set(case.window.context.slots) == {item["source_id"] for item in case.window.context.project_sources}
    assert next(iter(case.window.context.slots[old_key].collection)).translation == "Unsaved A"
    assert not case.window.context.dirty


def test_cancel_choice_keeps_the_existing_project_untouched(shared_plugin_window):
    case = shared_plugin_window
    other = write_plugin(case.root / "B.esp", [(0x900, "OtherNPC", "Other original")])
    before = case.services.project_lifecycle.active.variant.snapshot()

    _drop_choice(case, other, "cancel")

    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert case.window.foreground_worker is None
    assert case.window.mode_tabs.currentIndex() == 0


def test_invalid_plugin_addition_reports_error_and_preserves_dirty_work(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    _dirty(case)
    invalid = case.root / "Broken.esp"
    invalid.write_bytes(b"TES4" + b"\xff" * 24)
    before = case.services.project_lifecycle.active.variant.snapshot()
    old_slot = case.window.context.active_slot
    messages = []
    monkeypatch.setattr(case.window, "show_message", lambda text, **_: messages.append(text))

    _drop_choice(case, invalid, "add")
    _until(lambda: case.window.foreground_worker is None)

    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert case.window.context.active_slot is old_slot
    assert case.window.context.dirty
    assert not case.window.context.project_opening
    assert not case.window.context.project_view_failed
    assert any("添加插件失败" in text for text in messages)


def test_changed_target_during_save_prompt_does_not_save_another_variant(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    _dirty(case)
    calls = []

    def change_target(*_):
        # Model an independent authoritative navigation while the modal prompt is open.
        monkeypatch.setattr(
            type(case.window.context), "active_version_identity", property(lambda _: ("other", "variant"))
        )
        return QMessageBox.StandardButton.Save

    monkeypatch.setattr(QMessageBox, "question", change_target)
    monkeypatch.setattr(case.window, "save_current_project_async", lambda **kw: calls.append(kw))

    _drop_choice(case, case.plugin, "new")

    assert calls == []
    assert case.window.start_center_controller.guided_project.state.source_path is None


def test_non_plugin_drop_retains_the_existing_review_flow(shared_plugin_window):
    case = shared_plugin_window
    unknown = case.root / "unknown.txt"
    unknown.write_text("not a supported translation format", encoding="utf-8")
    controller = case.window._intent_composition._drop_controller
    seen = []
    timer = QTimer(case.window)
    timer.setSingleShot(True)

    def dismiss():
        dialog = controller.review_dialog
        if dialog is not None:
            seen.append((dialog.isVisible(), dialog._confirm.isEnabled()))
            dialog.reject()

    timer.timeout.connect(dismiss)
    timer.start(0)
    try:
        controller.binding.inspect_paths((str(unknown),))
    finally:
        timer.stop()
    assert seen == [(True, False)]
    assert controller.plugin_dialog is None
    assert case.window.context.active_project_id == case.original_project_id
