import threading
import time

from PyQt6.QtCore import QThreadPool, QTimer
from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.terminology.draft_write_queue import DraftWriteQueue
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert predicate()


def test_draft_writes_leave_event_loop_responsive_and_keep_submission_order():
    release = threading.Event()
    started = threading.Event()
    threads, saved, failures, ticks = [], [], [], []
    queue = DraftWriteQueue(on_started=lambda: None, on_saved=saved.append, on_error=failures.append)

    def first():
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return "first"

    try:
        queue.submit(first)
        queue.submit(lambda: "second")
        QTimer.singleShot(0, lambda: ticks.append("responsive"))
        _wait(lambda: started.is_set() and bool(ticks))
        assert saved == []
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        release.set()
        _wait(lambda: saved == ["first", "second"])
        assert failures == []
    finally:
        release.set()
        queue.close()
        QThreadPool.globalInstance().waitForDone(3000)


def test_failed_draft_write_reports_original_error_and_allows_next_write():
    saved, failures = [], []
    error = RuntimeError("draft revision changed")
    queue = DraftWriteQueue(on_started=lambda: None, on_saved=saved.append, on_error=failures.append)

    def fail():
        raise error

    try:
        queue.submit(fail)
        queue.submit(lambda: "retry")
        _wait(lambda: saved == ["retry"])
        assert failures == [error]
    finally:
        queue.close()


def test_closing_drops_queued_writes_and_ui_callbacks_but_does_not_interrupt_commit():
    started, release, committed = threading.Event(), threading.Event(), threading.Event()
    saved, failures, pending = [], [], []
    queue = DraftWriteQueue(on_started=lambda: None, on_saved=saved.append, on_error=failures.append)

    def commit():
        started.set()
        assert release.wait(3)
        committed.set()

    try:
        queue.submit(commit)
        queue.submit(lambda: pending.append("should not run"))
        _wait(started.is_set)
        queue.close()
        release.set()
        _wait(committed.is_set)
        QThreadPool.globalInstance().waitForDone(3000)
        _APP.processEvents()
        assert saved == failures == pending == []
    finally:
        release.set()
        queue.close()


def test_window_only_reports_saved_after_background_commit(monkeypatch):
    window = TerminologyWindow(
        TerminologyPresenter(TerminologyUiServices(), RequestContext("operator", project_id="p", variant_id="v"))
    )
    started, release = threading.Event(), threading.Event()
    refreshes = []
    monkeypatch.setattr(window, "_bind_existing_assets", lambda: refreshes.append("refresh"))

    def commit():
        started.set()
        assert release.wait(3)
        return object()

    try:
        window._run_draft_command(commit)
        _wait(started.is_set)
        assert window.operation_notice.message.text() == "正在保存人工调整…"
        assert refreshes == []
        release.set()
        _wait(lambda: bool(refreshes))
        assert "已保存到草稿" in window.operation_notice.message.text()
        assert refreshes == ["refresh"]
    finally:
        release.set()
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
