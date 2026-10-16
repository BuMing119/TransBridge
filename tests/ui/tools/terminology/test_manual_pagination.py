from __future__ import annotations

import sys
import threading
import time

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThreadPool
from PyQt6.QtWidgets import QApplication, QTableView, QWidget
import pytest

from transbridge.application.terminology import CursorStaleError, Page, SnapshotCursor
from transbridge.ui.tools.terminology.paged_models import KeysetPagedTableModel, PagedColumn
from transbridge.ui.tools.terminology.pagination_bar import PaginationBar

_APP = QApplication.instance() or QApplication([])


class _Pool:
    def __init__(self):
        self.pending = []

    def start(self, runnable):
        self.pending.append(runnable)

    def drain(self):
        while self.pending:
            self.pending.pop(0).run()
            _APP.processEvents()


class _Source:
    def __init__(self, size=501):
        self.items = tuple(f"Term {index}" for index in range(size))
        self.snapshot = "initial"
        self.requests = []
        self.failure = None

    def __call__(self, ref, request):
        self.requests.append((ref, request))
        if self.failure is not None:
            raise self.failure
        if request.cursor is not None and request.cursor.snapshot_digest != self.snapshot:
            raise CursorStaleError("source changed")
        start = 0 if request.cursor is None else int(request.cursor.stable_id)
        rows = self.items[start : start + request.limit]
        end = start + len(rows)
        following = (
            SnapshotCursor(self.snapshot, request.query_fingerprint, (str(end),), str(end))
            if end < len(self.items)
            else None
        )
        return Page(rows, self.snapshot, following, len(self.items))


@pytest.fixture
def paging():
    pool, source = _Pool(), _Source()
    model = KeysetPagedTableModel(
        source,
        (PagedColumn("term", "术语", str),),
        page_size=100,
        max_cached_pages=2,
        manual_paging=True,
        thread_pool=pool,
    )
    bar = PaginationBar(model)
    model.set_query("current-terms", query_fingerprint="search=Sword&scope=project&state=adopted")
    pool.drain()
    yield model, bar, source, pool
    model.close()
    bar.close()


def test_last_page_can_return_to_evicted_first_rows_without_automatic_scroll_loading(paging):
    model, bar, source, pool = paging
    view = QTableView()
    view.setModel(model)
    try:
        assert model.rowCount() == 100
        assert bar.range_label.text() == "1–100 / 共 501 条"
        assert not model.canFetchMore()
        view.scrollToBottom()
        model.fetchMore()
        pool.drain()
        assert len(source.requests) == 1
        for _ in range(5):
            bar.next_button.click()
            pool.drain()
            assert model.cached_page_count <= 2
        assert model.rowCount() == 1
        assert model.index(0, 0).data() == "Term 500"
        assert bar.range_label.text() == "501–501 / 共 501 条"
        assert not bar.next_button.isEnabled()
        bar.previous_button.click()
        pool.drain()
        assert model.index(0, 0).data() == "Term 400"
        assert len(source.requests) == 6  # Previous page is still cached.
        bar.first_button.click()
        pool.drain()
        assert len(source.requests) == 7  # The evicted first page is fetched by its saved address.
        assert model.index(0, 0).data() == "Term 0"
        assert not bar.previous_button.isEnabled()
        assert model.pagination.total == 501
        for _ in range(5):
            bar.next_button.click()
            pool.drain()
        assert model.index(0, 0).data() == "Term 500"
        assert model.cached_page_count == 2
    finally:
        view.close()


def test_refresh_retains_page_and_filters_and_stale_last_page_returns_to_first(paging):
    model, bar, source, pool = paging
    for _ in range(5):
        model.navigate_next()
        pool.drain()
    last_request = source.requests[-1]
    model.refresh()
    assert "正在加载" in bar.notice.text()
    assert not bar.next_button.isEnabled()
    pool.drain()
    assert source.requests[-1] == last_request
    assert model.index(0, 0).data() == "Term 500"
    source.snapshot = "changed"
    model.refresh()
    pool.drain()
    assert model.index(0, 0).data() == "Term 0"
    assert "已返回首页" in bar.notice.text()
    assert "筛选条件保持不变" in bar.notice.text()
    assert source.requests[-1][1].cursor is None
    assert source.requests[-1][1].query_fingerprint == last_request[1].query_fingerprint


def test_failed_navigation_keeps_current_rows_and_retry_loads_requested_page(paging):
    model, bar, source, pool = paging
    source.failure = RuntimeError("<server> unavailable")
    model.navigate_next()
    pool.drain()
    assert model.index(0, 0).data() == "Term 0"
    assert "<server> unavailable" in bar.notice.text()
    assert bar.refresh_button.text() == "重试加载"
    source.failure = None
    bar.refresh_button.click()
    pool.drain()
    assert model.index(0, 0).data() == "Term 100"
    assert model.last_error is None
    assert bar.notice.isHidden()


def test_replaced_query_and_closed_model_reject_queued_results_and_controls(paging):
    model, bar, source, pool = paging
    model.navigate_next()
    old_generation = model.generation
    source.snapshot = "new-filter"
    model.set_query("new-ref", query_fingerprint="search=Other")
    assert not model.accept_page(old_generation, Page(("old",), "initial"))
    assert not model.accept_error(old_generation, RuntimeError("old failure"))
    pool.drain()
    assert source.requests[-1][0] == "new-ref"
    assert source.requests[-1][1].query_fingerprint == "search=Other"
    model.navigate_next()
    current = model.generation
    model.close()
    pool.drain()
    assert not model.accept_page(current, Page(("late",), "new-filter"))
    assert not bar.next_button.isEnabled()
    assert not bar.refresh_button.isEnabled()


def test_first_page_stale_failure_is_reported_without_an_infinite_restart():
    pool = _Pool()
    calls = []

    def load(_ref, _request):
        calls.append(True)
        raise CursorStaleError("snapshot reference no longer available")

    model = KeysetPagedTableModel(load, (PagedColumn("value", "值", str),), manual_paging=True, thread_pool=pool)
    bar = PaginationBar(model)
    try:
        model.set_query("expired-ref")
        pool.drain()
        assert calls == [True]
        assert not model.is_loading
        assert "snapshot reference no longer available" in bar.notice.text()
        assert bar.refresh_button.isEnabled()
    finally:
        model.close()
        bar.close()


def test_parent_deletion_while_loading_cancels_delivery_without_qobject_errors(monkeypatch):
    failures = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: failures.append(args))
    entered, release = threading.Event(), threading.Event()
    pool, parent = QThreadPool(), QWidget()

    def load(_ref, _request):
        entered.set()
        assert release.wait(3)
        return Page(("late",), "snapshot", total=1)

    model = KeysetPagedTableModel(
        load,
        (PagedColumn("value", "值", str),),
        parent,
        manual_paging=True,
        thread_pool=pool,
    )
    try:
        model.set_query("ref")
        assert entered.wait(2)
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(model)
        release.set()
        assert pool.waitForDone(3000)
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            _APP.processEvents()
        assert failures == []
    finally:
        release.set()
        pool.waitForDone(3000)
