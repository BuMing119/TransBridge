from __future__ import annotations

from dataclasses import replace
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit
import pytest

from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import ROW_BATCH_SIZE, TranslationTable
from transbridge.ui.workbench.translation_table_columns import (
    COL_CHECK,
    COL_CONTEXT,
    COL_INDEX,
    COL_KEY,
    COL_MARK,
    COL_ORIGINAL,
    COL_TRANSLATION,
)
from transbridge.ui.workbench.translation_table_sorting import ordered_source_rows

_APP = QApplication.instance() or QApplication([])


def _entries(count: int) -> tuple[TranslationEntry, ...]:
    return tuple(
        TranslationEntry(str(index), f"key-{count - index:05d}", f"Original {index}", "", 0, "NPC_:FULL")
        for index in range(count)
    )


def _drain(table: TranslationTable) -> None:
    deadline = time.monotonic() + 10
    while table.has_pending_batch and time.monotonic() < deadline:
        _APP.processEvents()
    assert not table.has_pending_batch
    assert table.rowCount() == len(table.render_session.entries)


def _ids(table: TranslationTable) -> list[str]:
    return [table.item(row, COL_KEY).data(Qt.ItemDataRole.UserRole).id for row in range(table.rowCount())]


def _click(table: TranslationTable, column: int) -> None:
    header = table.horizontalHeader()
    position = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) // 2, header.height() // 2)
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)


@pytest.fixture
def table():
    view = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    view.resize(1_200, 500)
    view.show()
    _APP.processEvents()
    yield view
    view.close_rendering()
    view.close()
    view.deleteLater()
    _APP.processEvents()


def test_header_click_cycles_ascending_descending_default_without_source_mutation(table):
    entries = _entries(12)
    session = RenderSession(7, 42, entries)
    snapshot = [entry.to_dict() for entry in entries]
    table.start_render(session, {}, {})
    header = table.horizontalHeader()

    assert not header.isSortIndicatorShown()
    _click(table, COL_KEY)
    assert _ids(table) == [entry.id for entry in reversed(entries)]
    assert header.isSortIndicatorShown()
    assert header.sortIndicatorSection() == COL_KEY
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    assert table.item(0, COL_INDEX).text() == "12"
    _click(table, COL_CHECK)
    assert header.sortIndicatorSection() == COL_KEY
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    assert _ids(table) == [entry.id for entry in reversed(entries)]

    _click(table, COL_KEY)
    assert _ids(table) == [entry.id for entry in entries]
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    _click(table, COL_KEY)
    assert _ids(table) == [entry.id for entry in entries]
    assert not header.isSortIndicatorShown()
    assert table.render_session is session
    assert [entry.to_dict() for entry in entries] == snapshot
    assert not table.isSortingEnabled()  # Qt must not reorder incomplete item batches.


def test_index_and_label_counts_sort_numerically(table):
    entries = _entries(12)
    labels = {entries[0].id: {str(i) for i in range(10)}, entries[1].id: {"a", "b"}}
    table.start_render(RenderSession(1, None, entries), labels, {})

    _click(table, COL_MARK)
    assert _ids(table) == [entry.id for entry in entries[2:]] + [entries[1].id, entries[0].id]
    _click(table, COL_MARK)
    assert _ids(table) == [entry.id for entry in entries]
    _click(table, COL_INDEX)
    assert [table.item(row, COL_INDEX).text() for row in range(12)] == [str(i) for i in range(1, 13)]
    _click(table, COL_INDEX)
    assert [table.item(row, COL_INDEX).text() for row in range(12)] == [str(i) for i in range(12, 0, -1)]


@pytest.mark.parametrize(
    "column,field", [(COL_KEY, "key"), (COL_ORIGINAL, "original"), (COL_TRANSLATION, "translation")]
)
def test_text_sort_uses_full_text_casefold_empty_values_and_stable_ties(column, field):
    prefix = "A" * 81
    values = [prefix + "z", prefix.lower() + "a", "", prefix + "A", "中文"]
    # Keys are immutable domain identities; a blank input key falls back to id.
    entries = tuple(
        TranslationEntry(
            str(index),
            value if field == "key" else str(index),
            value if field == "original" else "",
            value if field == "translation" else "",
            0,
            None,
        )
        for index, value in enumerate(values)
    )

    assert ordered_source_rows(entries, {}, column) == (2, 1, 3, 0, 4)
    assert ordered_source_rows(entries, {}, column, True) == (4, 0, 1, 3, 2)
    assert ordered_source_rows(entries, {}, None) == (0, 1, 2, 3, 4)


def test_context_sorts_category_then_numeric_stage():
    entries = tuple(
        replace(entry, context=context, stage=stage)
        for entry, (context, stage) in zip(
            _entries(4), [("NPC_:FULL", 2), ("INFO:NAM1", 0), ("NPC_:FULL", 0), ("NPC_:FULL", 1)], strict=True
        )
    )
    from transbridge.ui.workbench.filters_presenter import entry_category

    expected = tuple(sorted(range(4), key=lambda row: (entry_category(entries[row]), entries[row].stage)))
    assert ordered_source_rows(entries, {}, COL_CONTEXT) == expected
    assert expected.index(2) < expected.index(3) < expected.index(0)


def test_sort_during_loading_preserves_selection_and_cancels_old_batches(table):
    entries = _entries(1_200)
    table.start_render(RenderSession(1, None, entries), {}, {})
    table.item(10, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    _click(table, COL_KEY)
    assert table.rowCount() == ROW_BATCH_SIZE
    assert table.has_pending_batch
    assert table.selected_entry_ids() == (entries[10].id,)
    assert _ids(table)[0] == entries[-1].id  # Includes entries outside the old rendered batch.

    # The selected entry is not materialized yet. A second click must retain it.
    _click(table, COL_KEY)
    _click(table, COL_KEY)
    _click(table, COL_KEY)
    _drain(table)
    assert _ids(table) == [entry.id for entry in reversed(entries)]
    assert table.selected_entry_ids() == (entries[10].id,)
    selected_row = table.find_entry_row(10, entries[10].id)
    assert selected_row == len(entries) - 11
    assert table.item(selected_row, COL_CHECK).checkState() == Qt.CheckState.Checked


def test_sort_is_retained_across_filter_refresh_and_default_restores_filtered_source_order(table):
    entries = _entries(600)
    table.start_render(RenderSession(1, None, entries), {}, {})
    _click(table, COL_KEY)
    filtered = entries[::3]
    table.start_render(RenderSession(2, 12, filtered), {}, {})
    _drain(table)
    assert _ids(table) == [entry.id for entry in reversed(filtered)]

    _click(table, COL_KEY)
    _click(table, COL_KEY)
    assert _ids(table) == [entry.id for entry in filtered]
    _click(table, COL_CHECK)
    assert not table.horizontalHeader().isSortIndicatorShown()


def test_locate_and_refresh_entry_use_sorted_identity(table):
    entries = _entries(600)
    table.start_render(RenderSession(1, None, entries), {}, {})
    _click(table, COL_KEY)
    table.locate_entry(entries[0].id)
    _drain(table)
    assert table.currentRow() == 599
    changed = replace(entries[0], translation="Changed", stage=STAGE_TRANSLATED)
    assert table.update_rendered_entry(changed, preferred_row=0) == 599
    assert table.item(599, COL_TRANSLATION).text() == "Changed"
    assert "已翻译" in table.item(599, COL_CONTEXT).text()
    assert table.item(0, COL_TRANSLATION).text() == "（无译文）"


def test_sort_preserves_all_selected_ids_during_and_after_loading(table):
    entries = _entries(1_000)
    table.start_render(RenderSession(1, None, entries), {}, {})
    _drain(table)
    table.setCurrentCell(100, COL_TRANSLATION)
    table.selectAll()
    _click(table, COL_KEY)
    assert set(table.selected_entry_ids()) == {entry.id for entry in entries}
    _drain(table)
    assert len(table.selectionModel().selectedRows()) == len(entries)
    assert table.currentRow() == 899
    assert table.verticalScrollBar().value() == 0


@pytest.mark.parametrize("clear_only", [False, True])
def test_new_selection_supersedes_pending_restoration(table, clear_only):
    entries = _entries(1_000)
    table.start_render(RenderSession(1, None, entries), {}, {})
    _drain(table)
    table.selectAll()
    _click(table, COL_KEY)
    if clear_only:
        table.clearSelection()
    else:
        item = table.item(0, COL_KEY)
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(item).center())
    _drain(table)
    assert table.selected_entry_ids() == (() if clear_only else (entries[-1].id,))


def test_sort_only_changes_view_and_edit_targets_correct_entry(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    widget = Step2PreviewWidget(context)
    collection = TranslationEntryCollection(_entries(12))
    widget.refresh(collection)
    before = tuple(collection)
    filtered = widget.filtered_entries()
    changes = []
    context.collection_changed.connect(lambda *_: changes.append(True))
    try:
        _click(widget._table, COL_KEY)
        assert tuple(collection) == before
        assert widget.filtered_entries() is filtered
        assert changes == []
        widget._table.item(0, COL_TRANSLATION).setText("译文")
        assert before[-1].translation == "译文"
        assert before[0].translation == ""
        assert widget._table.item(0, COL_KEY).data(Qt.ItemDataRole.UserRole) is before[-1]
    finally:
        widget.close()
        widget.deleteLater()
        _APP.processEvents()


def test_large_sort_yields_to_event_loop_and_can_close_mid_render(table):
    table.start_render(RenderSession(1, None, _entries(10_000)), {}, {})
    heartbeat_rows = []
    QTimer.singleShot(0, lambda: heartbeat_rows.append(table.rowCount()))
    _click(table, COL_KEY)
    assert table.rowCount() == ROW_BATCH_SIZE
    _APP.processEvents()
    assert heartbeat_rows and heartbeat_rows[0] < 10_000
    table.close_rendering()
    _APP.processEvents()
    assert not table.has_pending_batch
    assert table.render_session.entries == ()


def test_clicking_header_commits_the_active_editor_before_reordering(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    widget = Step2PreviewWidget(context_module.AppContext())
    entries = _entries(3)
    widget.resize(1_200, 700)
    widget.show()
    widget.refresh(TranslationEntryCollection(entries))
    _APP.processEvents()
    table = widget._table
    try:
        item = table.item(0, COL_TRANSLATION)
        table.setCurrentItem(item)
        table.editItem(item)
        editor = table.findChild(QLineEdit)
        assert editor is not None
        editor.setText("Edited before sort")
        _click(table, COL_KEY)
        _APP.processEvents()
        assert entries[0].translation == "Edited before sort"
        assert table.item(2, COL_TRANSLATION).text() == "Edited before sort"
    finally:
        widget.close()
        widget.deleteLater()
        _APP.processEvents()
