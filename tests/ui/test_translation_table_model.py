"""Virtual table resets and edits preserve complete model/view behavior."""

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtGui import QStandardItemModel
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTableView, QTableWidget
import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.translation_table_columns import COL_CHECK, COL_KEY, COL_TRANSLATION, NUM_COLUMNS


@pytest.fixture
def table():
    app = QApplication.instance() or QApplication([])
    view = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    yield view
    view.close_rendering()
    view.close()
    view.deleteLater()
    app.processEvents()


def _entries(count):
    return tuple(
        TranslationEntry(str(i), f"key-{i}", f"Original {i}", f"Translation {i}", 1, None) for i in range(count)
    )


def test_twenty_thousand_rows_replace_atomically_without_item_allocation(table):
    assert isinstance(table, QTableView)
    assert not isinstance(table, QTableWidget)
    entries = _entries(20_000)
    table.start_render(RenderSession(1, 1, entries), {}, {})
    model = table.model()
    assert model.rowCount() == len(entries)
    assert model.columnCount() == NUM_COLUMNS
    assert table.entry_at_row(19_999) is entries[-1]
    assert model.index(19_999, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == entries[-1].translation
    stale = QPersistentModelIndex(model.index(19_999, COL_KEY))
    table.selectRow(19_999)
    changes = []
    model.modelAboutToBeReset.connect(lambda: changes.append(("before", model.rowCount())))
    model.modelReset.connect(lambda: changes.append(("after", model.rowCount())))
    replacement = _entries(3)

    table.start_render(RenderSession(2, 2, replacement), {}, {})

    assert table.model() is model
    assert changes == [("before", 20_000), ("after", 3)]
    assert not stale.isValid()
    assert not model.index(3, COL_KEY).isValid()
    assert table.selected_entry_ids() == ()
    assert not table.has_pending_batch
    QApplication.processEvents()
    assert model.rowCount() == 3
    assert table.entry_at_row(2) is replacement[-1]


def test_model_edit_requests_authoritative_commit_before_display_changes(table):
    entry = _entries(1)[0]
    table.start_render(RenderSession(1, 1, (entry,)), {}, {})
    model = table.model()
    index = model.index(0, COL_TRANSLATION)
    requested = []
    table.translation_edit_requested.connect(lambda selected, text, row: requested.append((selected, text, row)))

    assert model.setData(index, "User draft", Qt.ItemDataRole.EditRole)

    assert requested == [(entry, "User draft", 0)]
    assert entry.translation == "Translation 0"
    assert index.data(Qt.ItemDataRole.DisplayRole) == "Translation 0"
    committed = replace(entry, translation="User draft")
    table.update_rendered_entry(committed)
    assert index.data(Qt.ItemDataRole.DisplayRole) == "User draft"
    assert table.entry_at_row(0) is committed
    assert not model.setData(model.index(0, COL_KEY), "wrong", Qt.ItemDataRole.EditRole)


def test_checkbox_and_row_selection_remain_bidirectionally_consistent(table):
    entries = _entries(3)
    table.start_render(RenderSession(1, 1, entries), {}, {})
    model = table.model()
    first = model.index(0, COL_CHECK)
    assert model.setData(first, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert table.selected_entry_ids() == (entries[0].id,)
    assert first.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    table.clearSelection()
    assert first.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
    table.selectAll()
    assert table.selected_entry_ids() == tuple(entry.id for entry in entries)
    assert all(
        model.index(row, COL_CHECK).data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked for row in range(3)
    )
    assert model.setData(first, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert table.selected_entry_ids() == tuple(entry.id for entry in entries[1:])


def test_checkbox_mouse_and_keyboard_toggle_the_same_selection(table):
    entries = _entries(3)
    table.start_render(RenderSession(1, 1, entries), {}, {})
    table.resize(900, 300)
    table.show()
    QApplication.processEvents()
    index = table.model().index(0, COL_CHECK)
    checkbox = table.visualRect(index).center()

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=checkbox)
    assert table.selected_entry_ids() == (entries[0].id,)
    assert index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=checkbox)
    assert table.selected_entry_ids() == ()
    assert index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked

    table.setCurrentIndex(index)
    table.clearSelection()
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Space)
    assert table.selected_entry_ids() == (entries[0].id,)
    assert index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked

    QTest.keyClick(table, Qt.Key.Key_Space)
    assert table.selected_entry_ids() == ()
    assert index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked


def test_old_and_foreign_indexes_cannot_edit_a_replacement_project(table):
    original = _entries(1)[0]
    table.start_render(RenderSession(1, 1, (original,)), {}, {})
    model = table.model()
    old_index = model.index(0, COL_TRANSLATION)
    replacement = replace(original, id="replacement", key="replacement-key", entry_key=None)
    table.start_render(RenderSession(2, 2, (replacement,)), {}, {})
    foreign_model = QStandardItemModel(1, NUM_COLUMNS)
    foreign_index = foreign_model.index(0, COL_TRANSLATION)
    requested = []
    table.translation_edit_requested.connect(lambda entry, text, row: requested.append((entry, text, row)))

    for rejected in (old_index, foreign_index):
        assert model.data(rejected, Qt.ItemDataRole.UserRole) is None
        assert model.flags(rejected) == Qt.ItemFlag.NoItemFlags
        assert not model.setData(rejected, "stale draft", Qt.ItemDataRole.EditRole)
        assert not table.activate_entry_editor(rejected)
    assert requested == []
    assert table.entry_at_row(0) is replacement
    assert replacement.translation == "Translation 0"

    assert model.setData(model.index(0, COL_TRANSLATION), "fresh draft", Qt.ItemDataRole.EditRole)
    assert requested == [(replacement, "fresh draft", 0)]


def test_replacing_session_rejects_changed_identity_and_order_without_reset(table):
    entries = _entries(2)
    session = RenderSession(1, 1, entries)
    table.start_render(session, {}, {})
    persistent = QPersistentModelIndex(table.model().index(0, COL_TRANSLATION))
    resets = []
    table.model().modelReset.connect(lambda: resets.append(True))

    for incompatible in (
        (replace(entries[0], key="different-key", entry_key=None), entries[1]),
        tuple(reversed(entries)),
    ):
        with pytest.raises(ValueError, match="unchanged row membership"):
            table.replace_session(RenderSession(2, 1, incompatible))
        assert table.render_session is session
        assert table.model().session is session
        assert persistent.isValid()
        assert persistent.data(Qt.ItemDataRole.UserRole) is entries[0]
        assert resets == []

    updated = replace(entries[0], translation="Updated text")
    table.replace_session(RenderSession(2, 1, (updated, entries[1])))
    assert persistent.isValid()
    assert persistent.data(Qt.ItemDataRole.DisplayRole) == "Updated text"
    assert resets == []
