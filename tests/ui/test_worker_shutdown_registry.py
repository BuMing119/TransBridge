from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget
import pytest

from transbridge.ui.shell import window_lifecycle
from transbridge.ui.shell.close_input_guard import CloseInputGuard
from transbridge.ui.shell.window_lifecycle import WindowLifecycle
from transbridge.ui.worker_registry import get_api_worker_registry
from transbridge.ui.workers import ApiWorker

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    assert predicate()


class CloseHost(QWidget):
    def __init__(self):
        super().__init__()
        self.workers = []
        self.project_open_worker = self.foreground_worker = None
        self.close_pending = self.close_ready = False
        self.order = []
        self.context = SimpleNamespace(dirty=False, workspace=None, close_projection=self._dispose_projection)
        self.project_coordinator = Mock()
        self.tool_windows = Mock()
        self.status_presenter = Mock()
        self.saveGeometry = Mock(return_value=b"")
        self.saveState = Mock(return_value=b"")
        self.workbench = QWidget(self)
        self.workbench.show_step2_progress = Mock()
        self.workbench.hide_step2_progress = Mock()
        self.lifecycle = WindowLifecycle(self)
        self.save_ok = True
        self.on_saved = lambda: None

    def _dispose_projection(self):
        self.order.append("disposed")

    def save_current_project_async(self, *, on_finished):
        self.order.append("save")
        worker = ApiWorker(lambda: self.save_ok, route_http_errors=False)
        self.workers.append(worker)

        def finished():
            self.on_saved()
            on_finished(self.save_ok)

        worker.finished.connect(finished)
        worker.start()
        return True

    def closeEvent(self, event):
        if self.lifecycle.close_event(event):
            self.order.append("closed")
            event.accept()


@pytest.fixture
def host(monkeypatch):
    _wait(lambda: not get_api_worker_registry().busy)
    monkeypatch.setattr(window_lifecycle, "QSettings", Mock())
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    window = CloseHost()
    window.show()
    yield window
    window.lifecycle.auto_saver.stop()
    window.lifecycle._input_guard.resume()
    window.lifecycle._close_ready = True
    window.close()
    window.deleteLater()
    _wait(lambda: not get_api_worker_registry().busy)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("owner", ["host", "child"])
def test_close_waits_for_generic_and_child_workers_before_final_save(host, owner):
    release = threading.Event()
    worker = ApiWorker(lambda: release.wait(5), route_http_errors=False)
    worker.result.connect(lambda _: host.order.append("result"))
    if owner == "host":
        host.workers.append(worker)
    else:
        host.child_workers = [worker]
    worker.start()
    try:
        assert not host.close()
        assert host.isVisible() and not host.isEnabled()
        assert not host.order
        # A stopped thread with undelivered GUI results must still block saving.
        release.set()
        assert worker.wait(2000)
        assert get_api_worker_registry().busy
        assert not host.order
        _wait(lambda: host.close_ready)
        assert host.order == ["result", "save", "disposed", "closed"]
    finally:
        release.set()
        worker.wait(2000)


def test_close_drains_follow_up_worker_started_by_result_handler(host):
    release = threading.Event()
    follow_up = ApiWorker(lambda: release.wait(5), route_http_errors=False)
    follow_up.result.connect(lambda _: host.order.append("follow-up"))
    worker = ApiWorker(lambda: None, route_http_errors=False)
    worker.result.connect(lambda _: follow_up.start())
    worker.start()
    try:
        host.close()
        _wait(follow_up.isRunning)
        assert not host.order
        release.set()
        _wait(lambda: host.close_ready)
        assert host.order == ["follow-up", "save", "disposed", "closed"]
    finally:
        release.set()
        worker.wait(2000)
        follow_up.wait(2000)


def test_close_waits_for_native_exit_after_finished_signal(host):
    release_work, native_cleanup, release_cleanup = threading.Event(), threading.Event(), threading.Event()
    worker = ApiWorker(lambda: release_work.wait(5), route_http_errors=False)

    def finish_native_cleanup():
        native_cleanup.set()
        assert release_cleanup.wait(5)

    worker.start()
    # Register after the registry's own native notification to expose early reaping.
    worker.finished.connect(finish_native_cleanup, Qt.ConnectionType.DirectConnection)
    release_work.set()
    try:
        assert native_cleanup.wait(2)
        assert not worker.isRunning() and not worker.wait(0)
        host.close()
        for _ in range(5):
            _APP.processEvents()
        assert get_api_worker_registry().is_pending(worker)
        assert host.order == []
        release_cleanup.set()
        _wait(lambda: host.close_ready)
        assert host.order == ["save", "disposed", "closed"]
    finally:
        release_work.set()
        release_cleanup.set()
        worker.wait(2000)


def test_close_waits_for_follow_up_started_by_final_save(host):
    release = threading.Event()
    follow_up = ApiWorker(lambda: release.wait(5), route_http_errors=False)
    follow_up.result.connect(lambda _: host.order.append("saved-follow-up"))
    host.on_saved = follow_up.start
    try:
        host.close()
        _wait(follow_up.isRunning)
        assert host.order == ["save"]
        assert not host.close_ready
        release.set()
        _wait(lambda: host.close_ready)
        assert host.order == ["save", "saved-follow-up", "disposed", "closed"]
    finally:
        release.set()
        follow_up.wait(2000)


def test_close_keeps_late_modal_confirmation_usable_and_waits_for_its_result(host):
    observed = []
    worker = ApiWorker(lambda: None, route_http_errors=False)

    def confirm(_):
        dialog = QDialog(host)

        def answer():
            observed.append((dialog.isEnabled(), list(host.order)))
            dialog.accept()

        QTimer.singleShot(250, answer)
        dialog.exec()
        host.order.append("confirmed")

    worker.result.connect(confirm)
    worker.start()
    host.close()
    _wait(lambda: host.close_ready)
    assert observed == [(True, [])]
    assert host.order == ["confirmed", "save", "disposed", "closed"]


def test_save_failure_restores_main_and_auxiliary_input(host):
    auxiliary = QWidget()
    auxiliary.show()
    host.save_ok = False
    try:
        host.close()
        assert not host.isEnabled() and not auxiliary.isEnabled()
        _wait(lambda: not host.close_pending)
        assert host.isVisible() and host.isEnabled() and auxiliary.isEnabled()
        assert host.order == ["save"]
        assert not host.close_ready
    finally:
        auxiliary.close()


def test_input_guard_preserves_preexisting_disabled_window():
    window = QWidget()
    window.setEnabled(False)
    window.show()
    guard = CloseInputGuard()
    try:
        guard.suspend()
        guard.resume()
        assert not window.isEnabled()
    finally:
        window.close()


def test_registry_retains_unreferenced_worker_until_native_finished_delivery():
    # A regression here aborts the Qt process, so exercise GC in isolation.
    script = """
import gc, threading, time, weakref
from PyQt6.QtWidgets import QApplication
from transbridge.ui.worker_registry import get_api_worker_registry
from transbridge.ui.workers import ApiWorker
app = QApplication([])
for _ in range(20):
    release = threading.Event()
    worker = ApiWorker(lambda: release.wait(5), route_http_errors=False)
    reference = weakref.ref(worker)
    worker.start()
    del worker
    gc.collect()
    try:
        assert reference() is not None
        assert get_api_worker_registry().busy
    finally:
        release.set()
        deadline = time.monotonic() + 5
        while get_api_worker_registry().busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
    assert not get_api_worker_registry().busy
    gc.collect()
    assert reference() is None
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_drains_error_and_deferred_deletion():
    errors = []

    def fail():
        raise ValueError("expected failure")

    worker = ApiWorker(fail, route_http_errors=False)
    worker.error.connect(errors.append)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    _wait(lambda: not get_api_worker_registry().busy)
    assert errors == ["expected failure"]
