"""Editor changes preserve table identity while respecting filter and sort results."""

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.io.identity import ExternalEntryRef
from transbridge.converter.translation_entry import TranslationEntry
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
    entries = [TranslationEntry(str(i), f"key-{i:04}", f"Original {i}", "old", 1, "NPC_:FULL") for i in range(600)]
    context.add_slot("fixture", context_module.CollectionSlot("Fixture", TranslationEntryCollection(entries)))
    yield view, context, entries
    view._table.close_rendering()
    view.close()


def change(context, entries, index, **values):
    entries[index] = replace(entries[index], **values)
    context.collection = TranslationEntryCollection(entries)


def test_patch_preserves_indices_selection_scroll_and_updates_offscreen_rows(preview):
    view, context, entries = preview
    table = view._table
    item = QPersistentModelIndex(table.model().index(0, COL_TRANSLATION))
    resets = []
    table.model().modelReset.connect(lambda: resets.append(True))
    table.selectRow(0)
    generation = table.render_session.generation
    change(context, entries, 0, translation="edited", stage=3)
    assert table.model().index(0, COL_TRANSLATION) == item
    assert item.isValid() and resets == []
    assert item.data(Qt.ItemDataRole.DisplayRole) == "edited"
    assert table.selected_entry_ids() == ("0",)
    assert table.render_session.generation == generation
    change(context, entries, 550, translation="later", stage=2)
    while table.has_pending_batch:
        _APP.processEvents()
    assert table.model().index(550, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "later"
    assert table.model().index(550, COL_KEY).data(Qt.ItemDataRole.UserRole) is entries[550]
    assert view._summary.needs_review == 1
    assert view._table_presenter.session.entries == table.render_session.entries


@pytest.mark.parametrize("filter_state", [{"stage": [1]}, {"search_trans": "old"}])
def test_filter_membership_change_falls_back_without_showing_stale_row(preview, filter_state):
    view, context, entries = preview
    view.apply_filter_state(filter_state)
    item = QPersistentModelIndex(view._table.model().index(0, COL_TRANSLATION))
    change(context, entries, 0, translation="new", stage=3)
    assert not item.isValid()
    assert entries[0].identity not in view.editor_navigation_keys()
    assert len(view.editor_navigation_keys()) == 599


def test_translation_sort_change_falls_back_and_navigation_uses_sorted_full_result(preview):
    view, context, entries = preview
    view._table.horizontalHeader().sectionClicked.emit(COL_TRANSLATION)
    item = QPersistentModelIndex(view._table.model().index(0, COL_TRANSLATION))
    change(context, entries, 0, translation="zzz")
    assert not item.isValid()
    assert len(view.editor_navigation_keys()) == 600
    assert view.editor_navigation_keys()[-1] == entries[0].identity


def test_return_preserves_scroll_filter_and_sort_and_handles_filtered_out_entry(preview):
    view, _context, entries = preview
    view.resize(900, 700)
    view.show()
    view._table.horizontalHeader().sectionClicked.emit(COL_KEY)
    view._table.horizontalHeader().sectionClicked.emit(COL_KEY)
    while view._table.has_pending_batch:
        _APP.processEvents()
    view._table.verticalScrollBar().setValue(20)
    before = view._table.verticalScrollBar().value()
    order = view.editor_navigation_keys()
    view.return_from_editor(entries[0].identity)
    assert view._table.verticalScrollBar().value() == before
    assert view.editor_navigation_keys() == order
    assert view._table.currentIndex().data(Qt.ItemDataRole.UserRole).identity == entries[0].identity
    view.apply_filter_state({"search_key": "key-0001"})
    view.return_from_editor(entries[0].identity)
    assert view.editor_navigation_keys() == (entries[1].identity,)


def test_return_to_offscreen_row_restores_current_index_without_scrolling(preview):
    view, _context, entries = preview
    table = view._table
    assert table.rowCount() == 600
    view.return_from_editor(entries[550].identity)
    while table.has_pending_batch:
        _APP.processEvents()
    assert table.currentIndex().data(Qt.ItemDataRole.UserRole).identity == entries[550].identity
    assert table.verticalScrollBar().value() == 0


@pytest.mark.parametrize("references", [False, True])
def test_bulk_reference_metadata_keeps_view_state_and_replaces_table_entries(preview, references):
    view, context, entries = preview
    view.resize(900, 700)
    view.show()
    view.apply_filter_state({"search_key": "key-00", "stage": [1], "search_trans": "old"})
    table = view._table
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    _APP.processEvents()
    table.selectRow(10)
    table.verticalScrollBar().setValue(20)
    item = QPersistentModelIndex(table.model().index(10, COL_KEY))
    selected = table.selected_entry_ids()
    order = view.editor_navigation_keys()
    scroll = table.verticalScrollBar().value()
    filters = view._filters_view.state()
    generation = table.render_session.generation
    summary = view._summary
    resets, row_updates = [], []
    table.model().modelReset.connect(lambda: resets.append(True))
    table.model().dataChanged.connect(lambda *_: row_updates.append(True))
    updated = [
        replace(
            entry,
            external_refs=(ExternalEntryRef("paratranz", "project:42", i + 1),) if references else (),
            revision=entry.revision.next(),
        )
        for i, entry in enumerate(entries)
    ]

    context.collection = TranslationEntryCollection(updated)

    assert item.isValid() and resets == []
    assert row_updates == []
    assert table.render_session.generation == generation
    assert table.selected_entry_ids() == selected
    assert table.verticalScrollBar().value() == scroll
    assert view.editor_navigation_keys() == order
    assert view._filters_view.state() == filters
    assert view._summary is summary
    assert view._entries == updated
    assert view._table_presenter.session is table.render_session
    for row in range(table.rowCount()):
        entry = table.model().index(row, COL_KEY).data(Qt.ItemDataRole.UserRole)
        assert entry is context.collection.get(entry.identity)
    assert context.collection.get(updated[550].identity) is updated[550]


@pytest.mark.parametrize("values", [{"translation": "new"}, {"stage": 3}])
def test_reference_batch_with_visible_edit_still_rechecks_filter_membership(preview, values):
    view, context, entries = preview
    view.apply_filter_state({"stage": [1], "search_trans": "old"})
    item = QPersistentModelIndex(view._table.model().index(0, COL_TRANSLATION))
    updated = [
        replace(entry, external_refs=(ExternalEntryRef("paratranz", "project:42", i + 1),))
        for i, entry in enumerate(entries)
    ]
    updated[0] = replace(updated[0], **values)

    context.collection = TranslationEntryCollection(updated)

    assert not item.isValid()
    assert len(view.editor_navigation_keys()) == len(entries) - 1
    assert entries[0].identity not in view.editor_navigation_keys()
