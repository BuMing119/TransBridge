"""Background refreshes must preserve the Workbench's active text input."""

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtGui import QInputMethodEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit
import pytest

from transbridge.converter.translation_entry import STAGE_QUESTIONABLE, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_KEY, COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    view = Step2PreviewWidget(context)
    entries = [TranslationEntry(str(i), f"key-{i}", f"Original {i}", "old translation", 1, None) for i in range(2)]
    context.add_slot("fixture", context_module.CollectionSlot("Fixture", TranslationEntryCollection(entries)))
    view.resize(1200, 800)
    view.show()
    view.activateWindow()
    _APP.processEvents()
    yield view, context, entries
    view.close()
    view.deleteLater()
    _APP.processEvents()


def _inline_editor(view):
    table = view._table
    table.setCurrentIndex(table.model().index(0, COL_TRANSLATION))
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_F2)
    _APP.processEvents()
    field = table.findChild(QLineEdit)
    assert field is not None
    field.selectAll()
    QTest.keyClicks(field, "pending draft")
    field.setSelection(2, 5)
    return field


def test_filter_refresh_preserves_search_focus_and_selection(preview):
    view, context, _entries = preview
    field = view._filters_view.search_all
    field.setFocus()
    QTest.keyClicks(field, "Original")
    field.setSelection(2, 3)

    view._filters_view.search_timer.timeout.emit()
    context.collection_changed.emit(context.collection)
    _APP.processEvents()

    assert field.hasFocus()
    assert field.text() == "Original"
    assert field.selectionStart() == 2
    assert field.selectedText() == "igi"


def test_unchanged_collection_refresh_preserves_inline_draft(preview):
    view, context, _entries = preview
    field = _inline_editor(view)

    context.collection_changed.emit(context.collection)
    _APP.processEvents()

    assert field.isVisible()
    assert field.hasFocus()
    assert field.text() == "pending draft"
    assert field.selectionStart() == 2
    assert field.selectedText() == "nding"
    field.undo()
    assert field.text() == "old translation"


def test_stage_refresh_preserves_inline_draft(preview):
    view, context, entries = preview
    field = _inline_editor(view)

    context.collection = TranslationEntryCollection((replace(entries[0], stage=3), entries[1]))
    _APP.processEvents()

    assert field.hasFocus()
    assert field.text() == "pending draft"
    assert field.selectionStart() == 2
    assert field.selectedText() == "nding"


def test_unchanged_collection_refresh_preserves_inline_input_method_composition(preview):
    view, context, _entries = preview
    field = _inline_editor(view)
    field.clear()
    empty_cursor = field.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
    _APP.sendEvent(field, QInputMethodEvent("han", []))
    composition_cursor = field.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle)
    assert composition_cursor != empty_cursor

    context.collection_changed.emit(context.collection)
    _APP.processEvents()

    assert field.hasFocus()
    assert field.text() == ""
    assert field.inputMethodQuery(Qt.InputMethodQuery.ImCursorRectangle) == composition_cursor
    commit = QInputMethodEvent()
    commit.setCommitString("寒")
    _APP.sendEvent(field, commit)
    assert field.text() == "寒"


def test_in_place_update_of_another_row_preserves_inline_draft_and_publishes_changes(preview):
    view, context, entries = preview
    field = _inline_editor(view)
    with pytest.deprecated_call():
        entries[1].translation = "external update"
    with pytest.deprecated_call():
        entries[1].stage = STAGE_QUESTIONABLE

    context.collection_changed.emit(context.collection)
    _APP.processEvents()

    assert field.isVisible() and field.hasFocus()
    assert field.text() == "pending draft"
    assert field.selectionStart() == 2
    assert field.selectedText() == "nding"
    assert view._table.model().index(1, COL_TRANSLATION).data() == "external update"
    assert view._summary.needs_review == 1


def test_external_translation_change_is_not_hidden_by_editor_data_guard(preview):
    view, context, entries = preview
    field = _inline_editor(view)
    with pytest.deprecated_call():
        entries[0].translation = "external update"

    context.collection_changed.emit(context.collection)
    _APP.processEvents()

    assert field.isVisible()
    assert field.text() == "external update"
    assert view._table.model().index(0, COL_TRANSLATION).data() == "external update"


def test_switching_content_with_identical_entry_keys_closes_the_old_editor(preview):
    view, context, entries = preview
    _inline_editor(view)
    old_index = QPersistentModelIndex(view._table.currentIndex())
    other = TranslationEntryCollection(replace(entry, translation="other content") for entry in entries)

    context.add_slot("other", context_module.CollectionSlot("Other", other))
    _APP.processEvents()

    assert not old_index.isValid()
    assert not any(field.isVisible() for field in view._table.findChildren(QLineEdit))
    assert all(entry.translation == "other content" for entry in context.collection)


@pytest.mark.parametrize("operation", ["sort", "filter"])
def test_sort_and_filter_membership_changes_still_invalidate_old_indices(preview, operation):
    view, _context, _entries = preview
    table = view._table
    old_index = QPersistentModelIndex(table.model().index(0, COL_TRANSLATION))

    if operation == "sort":
        table.horizontalHeader().sectionClicked.emit(COL_KEY)
        table.horizontalHeader().sectionClicked.emit(COL_KEY)
    else:
        view.apply_filter_state({"search_key": "key-1"})

    assert not old_index.isValid()
    assert table.entry_at_row(0).key == "key-1"


def test_activating_terminology_projection_closes_inline_editor(preview):
    view, _context, _entries = preview
    _inline_editor(view)
    table = view._table
    old_index = QPersistentModelIndex(table.currentIndex())

    table.set_terminology_projection({}, profile_label="Published terminology")
    table.start_render(table.render_session, {}, {})
    _APP.processEvents()

    assert not old_index.isValid()
    assert not any(field.isVisible() for field in table.findChildren(QLineEdit))
    assert not table.model().flags(table.model().index(0, COL_TRANSLATION)) & Qt.ItemFlag.ItemIsEditable
