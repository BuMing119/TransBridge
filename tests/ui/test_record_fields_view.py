"""The record browser preserves occurrences and shares the draft editor."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from tests.dialogue_support import dialogue_entry
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.dialogue.models import RecordFieldsModel
from transbridge.ui.dialogue.view import DialogueEditorView

_APP = QApplication.instance() or QApplication([])


def test_record_fields_keep_repeated_field_occurrences_and_exact_identities():
    entries = (dialogue_entry("QUST", index=1), dialogue_entry("QUST", index=2))
    model = RecordFieldsModel()
    model.set_entries(entries)

    assert model.rowCount() == 2
    assert [model.headerData(column, Qt.Orientation.Horizontal) for column in range(3)] == ["字段 ID", "原文", "译文"]
    assert model.index(0, 0).data() == "FULL [1]"
    assert model.index(1, 0).data() == "FULL [2]"
    assert [model.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(2)] == [
        entry.identity for entry in entries
    ]
    assert model.index(1, 0).data(Qt.ItemDataRole.ToolTipRole) == entries[1].key


def test_programmatic_browser_updates_are_silent_and_user_selection_emits():
    view = DialogueEditorView()
    modes, selected = [], []
    view.browse_mode_changed.connect(modes.append)
    view.record_entry_selected.connect(selected.append)
    entries = (dialogue_entry("QUST", index=1), dialogue_entry("QUST", index=2))
    try:
        view.set_record_available(True)
        view.set_browse_mode("record")
        view.show_record(entries, 1, record_type="QUST", editor_id="Quest", form_id="00000020")
        view.set_context_available(True, "任务索引已加载")
        assert view.browsing_mode == "record"
        assert modes == selected == []
        assert view.record_table.currentIndex().row() == 1
        assert all(value in view.record_heading.text() for value in ("QUST", "Quest", "00000020"))
        assert view.context_reason.isHidden()

        view.record_table.selectRow(0)
        assert selected == [0]
        view.browse_tabs.setCurrentIndex(1)
        assert modes == ["dialogue"]
        assert not view.context_reason.isHidden()
        view.set_context_available(False, "无任务关系")
        assert modes == ["dialogue"]
        assert view.browsing_mode == "record"
        assert view.context_reason.isHidden()
        view.set_record_available(False)
        assert view.browse_tabs.isHidden()
        assert not view.editor_panel.isHidden()
    finally:
        view.close()
        view.deleteLater()
        _APP.processEvents()


def test_record_field_label_uses_metadata_for_nonlegacy_identity():
    entry = TranslationEntry(
        id="opaque",
        key="opaque",
        original="Read this book",
        translation="",
        stage=0,
        context=None,
        metadata=(("plugin.field_type", "DESC"),),
    )
    model = RecordFieldsModel()
    model.set_entries((entry,))
    assert model.index(0, 0).data() == "DESC"


def test_browser_switch_keeps_shared_draft_selection_and_undo():
    view = DialogueEditorView()
    entry = dialogue_entry("QUST")
    try:
        view.body.setEnabled(True)
        view.set_record_available(True)
        view.show_entry(entry, "")
        view.translation.insertPlainText("pending draft")
        cursor = view.translation.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
        view.translation.setTextCursor(cursor)
        view.set_browse_mode("record")
        view.show_record((entry,), 0, record_type="QUST")
        view.set_browse_mode("dialogue")
        view.show_entry(entry, "pending draft")
        assert view.translation.toPlainText() == "pending draft"
        assert view.translation.textCursor().anchor() == 2
        assert view.translation.textCursor().position() == 7
        view.translation.undo()
        assert view.translation.toPlainText() == ""
    finally:
        view.close()
        view.deleteLater()
        _APP.processEvents()
