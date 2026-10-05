"""Editor changes preserve table identity while respecting filter and sort results."""

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

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


def test_patch_preserves_items_selection_scroll_and_updates_pending_batch(preview):
    view, context, entries = preview
    table = view._table
    item = table.item(0, COL_TRANSLATION)
    table.selectRow(0)
    generation = table.render_session.generation
    change(context, entries, 0, translation="edited", stage=3)
    assert table.item(0, COL_TRANSLATION) is item
    assert item.text() == "edited"
    assert table.selected_entry_ids() == ("0",)
    assert table.render_session.generation == generation
    change(context, entries, 550, translation="later", stage=2)
    while table.has_pending_batch:
        _APP.processEvents()
    assert table.item(550, COL_TRANSLATION).text() == "later"
    assert table.item(550, COL_KEY).data(Qt.ItemDataRole.UserRole) is entries[550]
    assert view._summary.needs_review == 1
    assert view._table_presenter.session.entries == table.render_session.entries


@pytest.mark.parametrize("filter_state", [{"stage": [1]}, {"search_trans": "old"}])
def test_filter_membership_change_falls_back_without_showing_stale_row(preview, filter_state):
    view, context, entries = preview
    view.apply_filter_state(filter_state)
    item = view._table.item(0, COL_TRANSLATION)
    change(context, entries, 0, translation="new", stage=3)
    assert sip.isdeleted(item)
    assert entries[0].identity not in view.editor_navigation_keys()
    assert len(view.editor_navigation_keys()) == 599


def test_translation_sort_change_falls_back_and_navigation_uses_sorted_full_result(preview):
    view, context, entries = preview
    view._table.horizontalHeader().sectionClicked.emit(COL_TRANSLATION)
    item = view._table.item(0, COL_TRANSLATION)
    change(context, entries, 0, translation="zzz")
    assert sip.isdeleted(item)
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
    assert view._table.currentItem().data(Qt.ItemDataRole.UserRole).identity == entries[0].identity
    view.apply_filter_state({"search_key": "key-0001"})
    view.return_from_editor(entries[0].identity)
    assert view.editor_navigation_keys() == (entries[1].identity,)


def test_return_to_pending_row_restores_current_item_after_batch_without_scrolling(preview):
    view, _context, entries = preview
    table = view._table
    assert table.rowCount() == 250
    view.return_from_editor(entries[550].identity)
    while table.has_pending_batch:
        _APP.processEvents()
    assert table.currentItem().data(Qt.ItemDataRole.UserRole).identity == entries[550].identity
    assert table.verticalScrollBar().value() == 0
