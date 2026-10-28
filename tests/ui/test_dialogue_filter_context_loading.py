"""Filtered dialogue entries retain task context while a source catalog loads."""

from threading import Event

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.dialogue_catalog_support import dialogue_plugin_bytes
from tests.dialogue_support import dialogue_entries
from tests.ui import test_dialogue_lazy_index as lazy_index_tests
from tests.ui.test_dialogue_editor import drain
from transbridge.application.dialogue import loading
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

lazy_editor = lazy_index_tests.lazy_editor


@pytest.mark.parametrize("source_kind", ["plugin", "snapshot"])
@pytest.mark.parametrize("fail", [False, True], ids=["complete", "catalog-error"])
def test_filtered_double_click_shows_task_tree_before_slow_catalog_finishes(
    lazy_editor, monkeypatch, source_kind, fail
):
    editor = lazy_editor
    slot = editor.context.active_slot
    if source_kind == "snapshot":
        slot.plugin = None
        slot.source_snapshot = SourceSnapshot.from_bytes(
            SourceDescriptor("fixture.esp"), FormatId.PLUGIN_SSE, dialogue_plugin_bytes()
        )
        editor.refresh()
    started, release = Event(), Event()
    read = loading.read_dialogue_catalog

    def slow_catalog(plugin):
        started.set()
        assert release.wait(5)
        if fail:
            raise ValueError("source catalog unavailable")
        return read(plugin)

    monkeypatch.setattr(loading, "read_dialogue_catalog", slow_catalog)
    preview = editor.preview
    editor.parent().resize(1200, 800)
    preview.resize(1200, 800)
    editor.parent().show()
    preview.show()
    target = dialogue_entries()[3]
    QTest.keyClicks(preview._filters_view.search_all, target.original)
    QTest.qWait(200)
    assert preview.filtered_entries() == (target,)
    table = preview._table
    point = table.visualRect(table.model().index(0, COL_ORIGINAL)).center()
    try:
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert started.wait(3)
        QApplication.processEvents()
        assert editor.dialog.isVisible()
        assert editor.view.context_panel.isVisible()
        assert editor.view.context_panel.isEnabled()
        assert editor.view.table_model.rowCount() == 3
        assert editor._current.before.entry_key == target.identity
        assert len(editor._navigation_keys) == len(dialogue_entries())
        assert editor.view.context_reason.isVisible()
        assert "补全" in editor.view.context_reason.text()
        editor.view.translation.setPlainText("保留这个草稿")
        cursor = editor.view.translation.textCursor()
        cursor.setPosition(3)
        editor.view.translation.setTextCursor(cursor)
    finally:
        release.set()
        drain(editor)
    assert editor.view.context_panel.isVisible()
    assert editor._current.before.entry_key == target.identity
    assert editor.view.translation.toPlainText() == "保留这个草稿"
    assert editor.view.translation.textCursor().position() == 3
    assert preview.get_filter_state()["search_all"] == target.original
    assert preview.filtered_entries() == (target,)
    if fail:
        assert editor.view.context_reason.isVisible()
        assert "source catalog unavailable" in editor.view.context_reason.text()
        assert editor.view.tree_model.rowCount() == 3
    else:
        assert editor.view.tree_model.rowCount() == 5
        assert not editor.view.context_reason.isVisible()


def test_unavailable_task_context_has_a_visible_reason_outside_the_hidden_tree(lazy_editor):
    editor = lazy_editor
    editor.context.active_slot.format_id = FormatId.XML_EET
    editor.refresh()
    editor.open_entry(dialogue_entries()[3].identity)
    QApplication.processEvents()
    assert not editor.view.context_splitter.isVisible()
    assert editor.view.translation.isVisible()
    assert editor.view.context_reason.isVisible()
    assert "EET" in editor.view.context_reason.text()
