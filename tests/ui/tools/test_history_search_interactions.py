"""Search actions cannot refresh the index or copy results from an older query."""

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.test_history_search_window import _hit, _Index, _Tasks
from transbridge.application.history_search import HistorySearchPage
from transbridge.application.tasks import OwnerRef
from transbridge.ui.tools.history_search import HistorySearchWindow


class _QueuedPool:
    def __init__(self):
        self.requests = []

    def start(self, runnable):
        self.requests.append(runnable)


class _RefreshTasks(_Tasks):
    refresh_count = 0

    def refresh(self, _owner):
        self.refresh_count += 1
        raise RuntimeError("unexpected refresh")


def _window(app):
    tasks = _RefreshTasks()
    window = HistorySearchWindow(_Index(), tasks, OwnerRef("owner", "history-search"))
    window._pool.waitForDone(3000)
    window.show()
    settle_layout(app)
    window._pool = _QueuedPool()
    return window, tasks


def test_enter_in_search_submits_one_query_without_triggering_index_refresh(layout_app):
    window, tasks = _window(layout_app)
    try:
        window.search_edit.setFocus()
        QTest.keyClicks(window.search_edit, "Dragon")
        QTest.keyClick(window.search_edit, Qt.Key.Key_Return)
        assert tasks.refresh_count == 0
        assert len(window._pool.requests) == 1
        QTest.qWait(300)
        assert len(window._pool.requests) == 1
    finally:
        window.close()


@pytest.mark.parametrize("changed", ["keyword", "scope", "kind"])
def test_changed_or_failed_query_cannot_copy_old_rows_and_retry_restores_selection(layout_app, changed):
    window, _tasks = _window(layout_app)
    try:
        original_generation = window._generation
        window._accept_query(original_generation, HistorySearchPage((_hit("previous translation"),), 1))
        assert window.copy_button.isEnabled()
        layout_app.clipboard().setText("untouched")
        if changed == "keyword":
            QTest.keyClicks(window.search_edit, "new query")
        else:
            combo = window.scope_combo if changed == "scope" else window.kind_combo
            QTest.keyClick(combo, Qt.Key.Key_Down)
        assert not window.copy_button.isEnabled()
        assert window.sources.count() == 0
        window.copy_translation()
        assert layout_app.clipboard().text() == "untouched"
        window._accept_query(original_generation, HistorySearchPage((_hit("late translation"),), 1))
        assert window.results.rowCount() == 0
        QTest.keyClick(window.search_edit, Qt.Key.Key_Return)
        window._reject_query(window._generation, RuntimeError("synthetic failure"))
        assert not window.copy_button.isEnabled()
        assert "失败" in window.status_label.text()
        QTest.keyClick(window.search_edit, Qt.Key.Key_Return)
        window._accept_query(window._generation, HistorySearchPage((_hit("current translation"),), 1))
        assert window.copy_button.isEnabled()
        QTest.mouseClick(window.copy_button, Qt.MouseButton.LeftButton)
        assert layout_app.clipboard().text() == "current translation"
        window.results.clearSelection()
        assert not window.copy_button.isEnabled()
        assert window.sources.count() == 0
    finally:
        window.close()
