"""Apply/continue, protected closing and compact layout work as one editing flow."""

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox

from tests.dialogue_support import dialogue_entries, dialogue_entry
from tests.ui import test_dialogue_editor as editor_tests
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.workbench.translation_table_columns import COL_KEY

editor = editor_tests.editor
drain = editor_tests.drain


def ordinary_entries(editor):
    entries = tuple(dialogue_entry("MGEF", f"{number:08X}", index=number) for number in range(1, 4))
    editor.context.collection = TranslationEntryCollection(entries)
    drain(editor)
    return entries


def test_apply_returns_to_workbench_without_resetting_filter(editor):
    entries = ordinary_entries(editor)
    editor.preview.apply_filter_state({"stage": [0]})
    editor.open_entry(entries[1].identity)
    editor.view.translation.setPlainText("应用后返回")
    QTest.mouseClick(editor.view.apply_button, Qt.MouseButton.LeftButton)
    drain(editor)
    assert not editor.dialog.isVisible()
    assert editor.context.collection.get(entries[1].identity).translation == "应用后返回"
    assert editor.preview.get_filter_state()["stage"] == [0]
    assert editor._current is None


def test_continue_focus_and_last_entry_completion(editor):
    entries = ordinary_entries(editor)
    editor.open_entry(entries[1].identity)
    editor.view.translation.setPlainText("第二条")
    QTest.mouseClick(editor.view.apply_next_button, Qt.MouseButton.LeftButton)
    drain(editor)
    assert editor.dialog.isVisible()
    assert editor._current.before.entry_key == entries[2].identity
    assert editor.view.translation.hasFocus()
    assert editor.view.apply_next_button.text() == "应用并返回"
    assert "3 / 3" in editor.view.navigation_label.text()
    editor.view.translation.setPlainText("第三条")
    QTest.mouseClick(editor.view.apply_next_button, Qt.MouseButton.LeftButton)
    assert not editor.dialog.isVisible()
    assert editor.context.collection.get(entries[2].identity).translation == "第三条"


def test_apply_keeps_other_drafts_when_close_is_cancelled(editor, monkeypatch):
    entries = ordinary_entries(editor)
    editor.open_entry(entries[0].identity)
    editor.view.translation.setPlainText("保留草稿")
    editor.move(1)
    editor.view.translation.setPlainText("提交当前条")
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Cancel)
    editor.apply()
    assert editor.dialog.isVisible()
    assert editor.context.collection.get(entries[1].identity).translation == "提交当前条"
    assert len(editor._drafts) == 1
    editor.move(-1)
    assert editor.view.translation.toPlainText() == "保留草稿"


def test_close_permission_alone_does_not_discard_drafts(editor, monkeypatch):
    entry = ordinary_entries(editor)[0]
    editor.open_entry(entry.identity)
    editor.view.translation.setPlainText("关闭失败后仍应保留")
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.StandardButton.Discard)
    assert editor.can_close()
    assert len(editor._drafts) == 1
    editor.dialog.close()
    assert not editor._drafts


def test_ordinary_navigation_follows_opening_filter_and_sort(editor):
    entries = ordinary_entries(editor)
    editor.context.collection = TranslationEntryCollection(
        replace(entry, stage=1) if entry == entries[1] else entry for entry in entries
    )
    editor.preview.apply_filter_state({"stage": [0]})
    header = editor.preview._table.horizontalHeader()
    header.sectionClicked.emit(COL_KEY)
    header.sectionClicked.emit(COL_KEY)
    editor.open_entry(entries[2].identity)
    assert editor._navigation_keys == (entries[2].identity, entries[0].identity)
    editor.view.translation.setPlainText("改变筛选成员")
    editor.apply(True)
    assert editor._current.before.entry_key == entries[0].identity
    assert "2 / 2" in editor.view.navigation_label.text()


def test_compact_layout_and_context_splitter_sizes_survive_switches(editor):
    target = dialogue_entries()[2]
    editor.open_entry(target.identity)
    editor.view.context_splitter.setSizes([220, 800])
    editor.view.vertical_splitter.setSizes([340, 330])
    horizontal = editor.view.context_splitter.sizes()
    vertical = editor.view.vertical_splitter.sizes()
    entries = ordinary_entries(editor)
    editor.open_entry(entries[0].identity)
    assert editor.view.context_splitter.isHidden()
    assert editor.view.translation.isVisible()
    editor.context.collection = TranslationEntryCollection(dialogue_entries())
    drain(editor)
    editor.open_entry(target.identity)
    assert not editor.view.context_splitter.isHidden()
    assert editor.view.context_splitter.sizes() == horizontal
    restored = editor.view.vertical_splitter.sizes()
    assert abs(restored[0] / sum(restored) - vertical[0] / sum(vertical)) < 0.01


def test_translation_apply_keeps_relationship_index_and_scene_navigation(editor):
    target = dialogue_entries()[2]
    editor.open_entry(target.identity)
    index = editor._index
    generation = editor._generation
    editor.view.translation.setPlainText("仅改变译文")
    editor.apply(True)
    assert editor._index is index
    assert editor._generation == generation
    assert editor._current.before.entry_key == dialogue_entries()[3].identity
    assert editor.context.collection.get(target.identity).translation == "仅改变译文"


def test_apply_synchronizes_matching_entries_before_returning(editor, monkeypatch):
    entries = ordinary_entries(editor)
    editor.context.collection = TranslationEntryCollection(replace(entry, original="Same") for entry in entries)
    monkeypatch.setattr("transbridge.ui.dialogue.sync_dialog.SyncTranslationDialog.exec", lambda self: 1)
    editor.open_entry(entries[0].identity)
    editor.view.translation.setPlainText("统一")
    editor.apply()
    assert all(entry.translation == "统一" for entry in editor.context.collection)
    assert not editor.dialog.isVisible()


def test_sync_failure_keeps_applied_entry_visible(editor, monkeypatch):
    entry = ordinary_entries(editor)[0]
    monkeypatch.setattr("transbridge.ui.dialogue.controller.synchronize_translation", lambda *a, **kw: "同步冲突")
    editor.open_entry(entry.identity)
    editor.view.translation.setPlainText("单条已应用")
    editor.apply()
    assert editor.dialog.isVisible()
    assert editor.context.collection.get(entry.identity).translation == "单条已应用"
    assert "同步冲突" in editor.view.message.text()
    assert editor.view.translation.toPlainText() == "单条已应用"


def test_removing_an_entry_prunes_opening_navigation_before_moving(editor):
    entries = ordinary_entries(editor)
    editor.open_entry(entries[0].identity)
    editor.context.collection = TranslationEntryCollection((entries[0], entries[2]))
    assert editor._navigation_keys == (entries[0].identity, entries[2].identity)
    editor.move(1)
    assert editor._current.before.entry_key == entries[2].identity
    assert "2 / 2" in editor.view.navigation_label.text()
