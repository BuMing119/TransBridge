import threading
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.ui.tools.terminology.search_test_support import wait_search
from transbridge.ui.tools.terminology import async_row_filter as filtering
from transbridge.ui.tools.terminology.row_search import SearchDocument

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    assert predicate()


def test_large_search_precomputes_once_and_debounces_on_worker_with_gui_delivery(monkeypatch):
    owner, pool = QWidget(), QThreadPool()
    search = filtering.AsyncRowFilter(owner, thread_pool=pool, small_limit=0)
    main_thread = threading.get_ident()
    prepared, matched, delivered = [], [], []
    match = filtering.match_search_index

    def document(row):
        prepared.append(threading.get_ident())
        return SearchDocument((row,))

    def matching(*args):
        matched.append((args[1], threading.get_ident()))
        return match(*args)

    monkeypatch.setattr(filtering, "match_search_index", matching)
    search.matches_changed.connect(lambda rows: delivered.append((rows, threading.get_ident())))
    try:
        search.set_rows(tuple(f"key-{index}" for index in range(300)), document)
        _wait(lambda: search._index is not None)
        search.set_query("k")
        search.set_query("key-2")
        search.set_query("KEY-299")
        assert search.is_pending
        assert search._timer.interval() == 200
        wait_search(search)
        assert tuple(delivered[-1][0].rows) == (299,)
        assert [query for query, _thread in matched] == ["key-299"]
        assert all(thread != main_thread for thread in prepared)
        assert all(thread != main_thread for _query, thread in matched)
        assert all(thread == main_thread for _rows, thread in delivered)
        search.set_query("key-298")
        wait_search(search)
        assert tuple(delivered[-1][0].rows) == (298,)
        assert len(prepared) == 300
        search.set_query("")
        assert len(delivered[-1][0].rows) == 300
    finally:
        owner.close()
        assert pool.waitForDone(3000)


@pytest.mark.parametrize("replace_rows", [False, True], ids=["new-query", "new-dataset"])
def test_queued_old_result_cannot_replace_new_query_or_dataset(replace_rows):
    owner, pool = QWidget(), QThreadPool()
    search = filtering.AsyncRowFilter(owner, thread_pool=pool, small_limit=0)
    delivered = []
    search.matches_changed.connect(delivered.append)

    def document(row):
        return SearchDocument((row,))

    try:
        search.set_rows(("Alpha", "Beta"), document)
        _wait(lambda: search._index is not None)
        search.set_query("Alpha", debounce=False)
        assert pool.waitForDone(3000)  # Old result is queued, not yet delivered to Qt.
        if replace_rows:
            search.set_rows(("Different", "Beta", "New value"), document)
            search.set_query("New value", debounce=False)
        else:
            search.set_query("Beta", debounce=False)
        observed = len(delivered)
        wait_search(search)
        assert tuple(delivered[-1].rows) == ((2,) if replace_rows else (1,))
        assert len(delivered) == observed + 1
    finally:
        owner.close()
        assert pool.waitForDone(3000)


def test_replacing_rows_rejects_already_queued_old_index():
    owner, pool = QWidget(), QThreadPool()
    search = filtering.AsyncRowFilter(owner, thread_pool=pool, small_limit=0)
    delivered = []
    search.matches_changed.connect(delivered.append)
    try:
        search.set_rows(("wrong", "old"), lambda row: SearchDocument((row,)))
        assert pool.waitForDone(3000)
        search.set_rows(("new",), lambda row: SearchDocument((row,)))
        search.set_query("new", debounce=False)
        wait_search(search)
        assert tuple(delivered[-1].rows) == (0,)
        assert search._index == (SearchDocument(("new",)),)
    finally:
        owner.close()
        assert pool.waitForDone(3000)


def test_closing_owner_stops_index_work_and_prevents_late_delivery():
    owner, pool = QWidget(), QThreadPool()
    search = filtering.AsyncRowFilter(owner, thread_pool=pool, small_limit=0)
    entered, release = threading.Event(), threading.Event()
    prepared, delivered = [], []
    search.matches_changed.connect(delivered.append)

    def document(row):
        prepared.append(row)
        entered.set()
        assert release.wait(3)
        return SearchDocument((row,))

    try:
        search.set_rows(tuple(str(i) for i in range(500)), document)
        assert entered.wait(2)
        before = len(delivered)
        owner.close()
        release.set()
        assert pool.waitForDone(3000)
        _APP.processEvents()
        assert len(prepared) == 1
        assert len(delivered) == before
        assert not search.is_pending
    finally:
        release.set()
        owner.close()
        assert pool.waitForDone(3000)


def test_background_index_failure_is_visible_and_retains_original_error():
    owner, pool = QWidget(), QThreadPool()
    search = filtering.AsyncRowFilter(owner, thread_pool=pool, small_limit=0)
    failure = ValueError("invalid search row")
    statuses = []
    search.status_changed.connect(lambda pending, error: statuses.append((pending, error)))

    def document(_row):
        raise failure

    try:
        search.set_rows(("row",), document)
        search.set_query("row")
        _wait(lambda: search.last_error is not None)
        assert search.last_error is failure
        assert not search.is_pending
        assert "invalid search row" in statuses[-1][1]
    finally:
        owner.close()
        assert pool.waitForDone(3000)
