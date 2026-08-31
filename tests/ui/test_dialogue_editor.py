from __future__ import annotations

from dataclasses import replace
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox, QWidget
import pytest

from tests.dialogue_support import dialogue_entries, dialogue_entry
from transbridge.application.dialogue.index import build_dialogue_index
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.dialogue.editing import EntryDraft
from transbridge.ui.shell.navigation_rail import WorkspaceShell
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import (
    COL_CHECK,
    COL_CONTEXT,
    COL_INDEX,
    COL_KEY,
    COL_MARK,
    COL_ORIGINAL,
    COL_TRANSLATION,
)

_APP = QApplication.instance() or QApplication([])


def drain(controller):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _APP.processEvents()
        if all(worker.isFinished() for worker in controller._workers):
            _APP.processEvents()
            return
        QTest.qWait(1)
    pytest.fail("dialogue worker did not finish")


@pytest.fixture
def editor(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    context.variant_store = SimpleNamespace(dirty=False)
    collection = TranslationEntryCollection(dialogue_entries())
    context.add_slot("fixture.esp", context_module.CollectionSlot("测试插件", collection, esp_path="fixture.esp"))
    shell = WorkspaceShell()
    preview = Step2PreviewWidget(context)
    preview.refresh(collection)
    shell.addTab(preview, "工作台")
    shell.addTab(QWidget(), "ParaTranz")
    shell.addTab(QWidget(), "开始")
    controller = DialogueEditorController(context, shell, preview, [])
    shell.resize(1200, 800)
    shell.show()
    drain(controller)
    yield controller
    drain(controller)
    controller.close()
    preview.close()
    shell.close()
    shell.deleteLater()
    _APP.processEvents()


def test_double_click_opens_correct_response_after_main_table_sorting(editor):
    target = dialogue_entries()[3]
    table = editor.preview._table
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    _APP.processEvents()
    row = next(
        row
        for row in range(table.rowCount())
        if table.item(row, COL_KEY).data(Qt.ItemDataRole.UserRole).identity == target.identity
    )
    point = table.visualItemRect(table.item(row, COL_TRANSLATION)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    _APP.processEvents()
    assert editor.dialog.isVisible() and editor.dialog.isWindow()
    assert not editor.dialog.isModal()
    assert editor.parent().currentIndex() == 0
    assert editor.parent().pages.count() == 3
    assert editor._current.before.entry_key == target.identity
    assert editor.view.table_model.rowCount() == 3
    assert editor.view.original.toPlainText() == target.original
    assert not table.findChildren(QLineEdit)


@pytest.mark.parametrize("column", [COL_INDEX, COL_MARK, COL_KEY, COL_ORIGINAL, COL_TRANSLATION, COL_CONTEXT])
@pytest.mark.parametrize("refresh_between_clicks", [False, True])
def test_double_click_any_entry_cell_opens_once_even_after_table_refresh(editor, column, refresh_between_clicks):
    table = editor.preview._table
    target = dialogue_entries()[2]
    point = table.visualItemRect(table.item(2, column)).center()
    requested = []
    editor.preview.entry_edit_requested.connect(requested.append)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    # A queued label/projection refresh may replace items between the two clicks.
    if refresh_between_clicks:
        editor.preview.refresh(editor.context.collection)
        _APP.processEvents()
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    _APP.processEvents()
    assert editor.dialog.isVisible()
    assert editor.parent().currentIndex() == 0
    assert editor._current.before.entry_key == target.identity
    assert requested == [target.identity]
    assert not table.findChildren(QLineEdit)


def test_double_click_checkbox_keeps_selection_without_opening_editor(editor):
    table = editor.preview._table
    point = table.visualItemRect(table.item(2, COL_CHECK)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not editor.dialog.isVisible()
    assert editor.parent().currentIndex() == 0


@pytest.mark.parametrize("close_action", ["window", "escape", "button"])
def test_popup_close_paths_protect_drafts_and_reopen_same_window(editor, monkeypatch, close_action):
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    dialog = editor.dialog
    editor.view.translation.setPlainText("未应用草稿")

    def close_popup():
        if close_action == "escape":
            QTest.keyClick(editor.view.translation, Qt.Key.Key_Escape)
        elif close_action == "button":
            editor.view.close_requested.emit()
        else:
            dialog.close()
        _APP.processEvents()

    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Cancel)
    close_popup()
    assert dialog.isVisible()
    assert editor.view.translation.toPlainText() == "未应用草稿"
    assert editor.context.collection.get(target).translation == ""
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Discard)
    close_popup()
    assert not dialog.isVisible()
    editor.open_entry(target)
    assert editor.dialog is dialog and dialog.isVisible()
    assert editor.view.translation.toPlainText() == ""
    assert editor.parent().pages.count() == 3
    assert [button.text() for button in editor.parent().navigation._page_buttons] == ["开始", "工作台", "ParaTranz"]


def test_popup_opens_while_index_loads_and_does_not_reopen_after_closing(editor):
    target = dialogue_entries()[2].identity
    editor.context.collection_changed.emit(editor.context.collection)
    editor.open_entry(target)
    assert editor.dialog.isVisible()
    assert editor._current.before.entry_key == target
    editor.view.translation.setPlainText("索引完成前的草稿")
    cursor = editor.view.translation.textCursor()
    cursor.setPosition(3)
    editor.view.translation.setTextCursor(cursor)
    drain(editor)
    assert editor.view.context_panel.isEnabled()
    assert editor.view.translation.toPlainText() == "索引完成前的草稿"
    assert editor.view.translation.textCursor().position() == 3
    editor.discard()
    editor.context.collection_changed.emit(editor.context.collection)
    editor.dialog.close()
    drain(editor)
    assert not editor.dialog.isVisible()
    assert editor._current is None


def test_popup_can_apply_and_advance_between_ordinary_entries(editor):
    first = dialogue_entry(kind="MGEF")
    second = dialogue_entry(kind="SPEL", form="00000021")
    editor.context.collection = TranslationEntryCollection((first, second))
    drain(editor)
    editor.open_entry(first.identity)
    dialog = editor.dialog
    editor.view.translation.setPlainText("普通译文")
    editor.view.apply_next_button.click()
    drain(editor)
    assert editor.context.collection.get(first.identity).translation == "普通译文"
    assert editor._current.before.entry_key == second.identity
    assert editor.dialog is dialog and dialog.isVisible()
    assert not editor.view.previous_button.isHidden()
    assert editor.view.previous_button.isEnabled()
    assert not editor.view.next_button.isEnabled()
    editor.view.previous_button.click()
    assert editor._current.before.entry_key == first.identity


def test_eet_opens_editor_with_disabled_task_tree_and_can_apply_translation(editor):
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    context = editor.context
    context.add_slot(
        "source.xml",
        context_module.CollectionSlot(
            "EET", TranslationEntryCollection(dialogue_entries()), eet_path="source.xml", format_id="xml.eet"
        ),
    )
    table = editor.preview._table
    item = table.item(2, COL_TRANSLATION)
    point = table.visualItemRect(item).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert editor.dialog.isVisible()
    assert editor.parent().currentIndex() == 0
    assert not editor.view.context_panel.isEnabled()
    assert "EET" in editor.view.context_panel.toolTip()
    assert editor.view.tree_model.rowCount() == 0
    assert editor.view.translation.isEnabled()
    assert not table.findChildren(QLineEdit)
    editor.select_quest(0)
    assert not editor.view.context_panel.isEnabled()
    editor.view.translation.setPlainText("EET 译文")
    editor.apply()
    assert context.collection.get(target).translation == "EET 译文"
    assert table.item(2, COL_TRANSLATION).text() == "EET 译文"


def test_plugin_with_eet_overlay_reenables_and_late_worker_cannot_reenable_eet(editor):
    context = editor.context
    old_generation, old_scope = editor._generation, editor._scope
    old_index = build_dialogue_index(dialogue_entries())
    context.add_slot(
        "source.xml", context_module.CollectionSlot("EET", TranslationEntryCollection(), eet_path="source.xml")
    )
    editor._loaded(old_generation, old_scope, old_index)
    assert not editor.view.context_panel.isEnabled()
    context.slots["fixture.esp"].eet_path = "translated.xml"
    context.activate_slot("fixture.esp")
    drain(editor)
    editor.open_entry(dialogue_entries()[2].identity)
    assert editor.view.context_panel.isEnabled()


def test_multiline_draft_survives_topic_switch_and_applies_to_main_table(editor):
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    text = "  第一行\n第二行 <Alias=Player>  "
    editor.view.translation.setPlainText(text)
    assert editor.context.collection.get(target).translation == ""
    editor.open_entry(dialogue_entries()[-1].identity)
    editor.open_entry(target)
    assert editor.view.translation.toPlainText() == text
    editor.apply(True)
    drain(editor)
    assert editor.context.collection.get(target).translation == text
    assert editor.context.collection.get(target).stage == 1
    assert editor.context.dirty
    assert editor._current.before.entry_key == dialogue_entries()[3].identity
    assert not editor._drafts
    table = editor.preview._table
    row = table.find_entry_row(-1, editor.context.collection.get(target).id)
    assert table.item(row, COL_TRANSLATION).text() == text


def test_draft_is_retained_across_source_switch_and_close_requires_a_decision(editor, monkeypatch):
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    editor.view.translation.setPlainText("草稿")
    draft = editor._current
    editor.context.add_slot(
        "other.xml",
        context_module.CollectionSlot("EET", TranslationEntryCollection(dialogue_entries()), eet_path="other.xml"),
    )
    assert "切换" in draft.commit(editor.context)
    assert editor.context.collection.get(target).translation == ""
    editor.context.activate_slot("fixture.esp")
    drain(editor)
    editor.open_entry(target)
    assert editor.view.translation.toPlainText() == "草稿"
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Cancel)
    assert not editor.can_close()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Discard)
    assert editor.can_close()


def test_external_change_rejects_stale_draft_without_losing_it(editor):
    target = dialogue_entries()[2].identity
    editor.open_entry(target)
    editor.view.translation.setPlainText("旧页面草稿")
    collection = editor.context.collection
    editor.context.collection = TranslationEntryCollection(
        replace(e, translation="外部新译文") if e.identity == target else e for e in collection
    )
    drain(editor)
    editor.open_entry(target)
    editor.apply()
    assert editor.context.collection.get(target).translation == "外部新译文"
    assert editor.view.translation.toPlainText() == "旧页面草稿"
    assert "修改" in editor.view.message.text()
    editor.discard()
    assert editor.view.translation.toPlainText() == "外部新译文"


def test_navigation_crosses_topic_boundary_and_f2_remains_inline(editor):
    editor.open_entry(dialogue_entries()[3].identity)
    editor.move(1)
    assert editor._current.before.entry_key == dialogue_entries()[4].identity
    editor.move(-1)
    assert editor._current.before.entry_key == dialogue_entries()[3].identity
    editor.dialog.close()
    table = editor.preview._table
    table.setCurrentCell(1, COL_TRANSLATION)
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_F2)
    assert table.findChildren(QLineEdit)


def test_ordinary_entry_opens_popup_without_task_tree_and_empty_content_clears_editor(editor):
    ordinary = dialogue_entry(kind="MGEF")
    editor.context.collection = TranslationEntryCollection((ordinary,))
    drain(editor)
    assert not editor.dialog.isVisible()
    table = editor.preview._table
    point = table.visualItemRect(table.item(0, COL_ORIGINAL)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert editor.dialog.isVisible()
    assert not editor.view.context_panel.isEnabled()
    assert editor.view.original.toPlainText() == ordinary.original
    assert editor.view.translation.isEnabled()
    editor.view.translation.setPlainText("普通词条译文")
    editor.apply()
    drain(editor)
    assert editor.context.collection.get(ordinary.identity).translation == "普通词条译文"
    editor.context.collection = TranslationEntryCollection()
    drain(editor)
    assert editor._current is None
    assert not editor.view.translation.isEnabled()
    assert "没有" in editor.view.message.text()


def test_foreground_and_close_save_lock_also_disables_dialogue_editor(editor):
    editor.open_entry(dialogue_entries()[2].identity)
    editor.preview.setEnabled(False)
    assert not editor.view.translation.isEnabled()
    assert not editor.view.apply_next_button.isEnabled()
    editor.preview.setEnabled(True)
    assert editor.view.translation.isEnabled()


def test_unchanged_apply_and_empty_translation_preserve_existing_stage(editor):
    entry = dialogue_entries()[2]
    editor.context.collection = TranslationEntryCollection([replace(entry, translation="已有", stage=3)])
    drain(editor)
    editor.open_entry(entry.identity)
    draft = EntryDraft.capture(editor.context, editor.context.collection.get(entry.identity))
    assert draft.commit(editor.context) is None
    editor.view.translation.setPlainText("")
    editor.apply()
    drain(editor)
    assert editor.context.collection.get(entry.identity).translation == ""
    assert editor.context.collection.get(entry.identity).stage == 3


def test_main_window_double_click_opens_child_window_without_adding_navigation(monkeypatch):
    from transbridge.ui.main_window import MainWindow

    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    monkeypatch.setattr("transbridge.ui.shell.window_lifecycle.WindowLifecycle.restore_state", lambda _: None)
    monkeypatch.setattr("transbridge.ui.shell.window_lifecycle.WindowLifecycle.start", lambda _: None)
    monkeypatch.setattr("transbridge.ui.shell.window_lifecycle.WindowLifecycle.close_event", lambda *_: True)
    monkeypatch.setattr(
        "transbridge.ui.coordinators.project_coordinator.ProjectCoordinator.init_workspace", lambda *_args, **_kw: None
    )
    window = MainWindow()
    entry = dialogue_entry(kind="MGEF")
    try:
        window.context.add_slot(
            "fixture.esp",
            context_module.CollectionSlot("插件", TranslationEntryCollection((entry,)), esp_path="fixture.esp"),
        )
        window.show()
        drain(window._dialogue_editor)
        assert not window._dialogue_editor.dialog.isVisible()
        table = window.workbench.preview._table
        point = table.visualItemRect(table.item(0, COL_ORIGINAL)).center()
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        dialog = window._dialogue_editor.dialog
        assert dialog.isVisible() and dialog.isWindow()
        assert dialog.parentWidget() is window
        assert window.mode_tabs.pages.count() == 3
        assert window.mode_tabs.currentIndex() == 0
        assert window._dialogue_editor.view.original.toPlainText() == entry.original
        dialog.close()
        assert not dialog.isVisible()
        assert table.currentRow() == 0
    finally:
        drain(window._dialogue_editor)
        window.close()
        window.deleteLater()
        _APP.processEvents()
