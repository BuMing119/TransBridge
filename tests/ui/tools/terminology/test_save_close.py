import threading
import time

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.ui.tools.terminology.draft_write_queue import DraftWriteQueue
from transbridge.ui.tools.terminology.operation_notice import OperationNotice
from transbridge.ui.tools.terminology.save_close import DraftSaveCloseGuard

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert predicate()


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.workspace = QWidget(self)
        self.operation_notice = OperationNotice(self)
        self.saved, self.errors = [], []
        self.queue = DraftWriteQueue(
            self, on_started=lambda: None, on_saved=self.saved.append, on_error=self.errors.append
        )
        self.guard = DraftSaveCloseGuard(self, self.queue)
        self.closed = False

    def closeEvent(self, event):  # noqa: N802
        if self.guard.allow_close():
            self.closed = True
            self.queue.close()
            event.accept()
        else:
            event.ignore()


def _scenario(monkeypatch, action, *, fail=False):
    monkeypatch.setattr("transbridge.ui.tools.terminology.save_close.choose_pending_save_action", lambda *_args: action)
    view = _Window()
    started, release = threading.Event(), threading.Event()

    def first():
        started.set()
        assert release.wait(3)
        if fail:
            raise RuntimeError("disk is read-only")
        return "first"

    view.queue.submit(first)
    view.queue.submit(lambda: "second")
    _wait(started.is_set)
    return view, release


def test_waiting_close_persists_every_accepted_write_without_blocking_ui(monkeypatch):
    view, release = _scenario(monkeypatch, "wait")
    try:
        view.close()
        assert not view.closed
        assert not view.workspace.isEnabled()
        assert view.queue.pending_count == 2
        release.set()
        _wait(lambda: view.closed)
        assert view.saved == ["first", "second"]
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(3000)
        view.queue.close()


def test_explicit_discard_only_withdraws_waiting_writes(monkeypatch):
    view, release = _scenario(monkeypatch, "discard")
    try:
        view.close()
        assert view.queue.pending_count == 1
        assert not view.closed
        release.set()
        _wait(lambda: view.closed)
        assert view.saved == ["first"]
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(3000)
        view.queue.close()


def test_cancelled_close_keeps_queued_edits_and_editing_enabled(monkeypatch):
    view, release = _scenario(monkeypatch, "cancel")
    try:
        view.close()
        assert not view.closed and view.workspace.isEnabled()
        release.set()
        _wait(lambda: view.saved == ["first", "second"])
        assert not view.closed
        view.close()
        assert view.closed
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(3000)
        view.queue.close()


def test_failed_save_cancels_automatic_close_and_keeps_original_reason(monkeypatch):
    view, release = _scenario(monkeypatch, "wait", fail=True)
    try:
        view.close()
        release.set()
        _wait(lambda: view.saved == ["second"])
        assert not view.closed and view.workspace.isEnabled()
        assert "disk is read-only" in view.operation_notice.message.text()
        assert len(view.errors) == 1
    finally:
        release.set()
        QThreadPool.globalInstance().waitForDone(3000)
        view.queue.close()


def test_completion_callback_can_enqueue_another_write_without_losing_callbacks():
    saved, callbacks = [], []
    queue = DraftWriteQueue(on_started=lambda: None, on_saved=saved.append, on_error=lambda error: None)
    try:
        queue.submit(
            lambda: "first",
            on_saved=lambda _value: queue.submit(lambda: "second", on_saved=callbacks.append),
        )
        _wait(lambda: callbacks == ["second"])
        assert saved == ["first", "second"]
        assert queue.pending_count == 0
    finally:
        queue.close()
