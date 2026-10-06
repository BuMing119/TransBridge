from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPalette, QStandardItem, QStandardItemModel
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QStyle, QStyleOptionViewItem
import pytest

from transbridge.ui.paratranz import export_tab
from transbridge.ui.paratranz._strings_common import _KEY_ROLE
from transbridge.ui.paratranz.string_detail_dialog import StringDetailDialog
from transbridge.ui.paratranz.string_navigation import NavItemDelegate
from transbridge.ui.worker_registry import get_api_worker_registry
from transbridge.ui.workers import ApiWorker

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    assert predicate()


class _Context(QObject):
    project_selected = pyqtSignal(object)
    paratranz_permissions_changed = pyqtSignal()

    config = SimpleNamespace(token="")
    current_user = None

    @staticmethod
    def is_admin():
        return True


def test_export_progress_updates_real_widget_only_on_gui_thread(monkeypatch):
    release = threading.Event()
    reported = threading.Event()
    threads = []

    class Workflow:
        def __init__(self, config):
            pass

        def trigger_and_download(self, pid, path, *, progress_callback):
            progress_callback("正在打包测试内容")
            reported.set()
            assert release.wait(3)
            return path

    class ExportView(export_tab.ExportTab):
        def _set_status(self, message):
            threads.append(QThread.currentThread())
            super()._set_status(message)

    context = _Context()
    widget = ExportView(context)
    widget._project_id = 1
    monkeypatch.setattr(export_tab, "ArtifactWorkflow", Workflow)
    monkeypatch.setattr(export_tab.QFileDialog, "getSaveFileName", lambda *args: ("mock.zip", ""))
    monkeypatch.setattr(widget, "load_artifacts", lambda: None)
    try:
        widget._trigger_export()
        assert reported.wait(1)
        _until(lambda: widget._status_lbl.toolTip() == "正在打包测试内容")
        assert threads and all(thread is _APP.thread() for thread in threads)
    finally:
        release.set()
        for worker in widget._workers:
            assert worker.wait(3000)
        _APP.processEvents()
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("exit_method", ["close", "escape", "reject", "accept", "done"])
def test_every_string_dialog_exit_waits_without_blocking_and_preserves_result(exit_method):
    context = _Context()
    dialog = StringDetailDialog([{"id": 1, "key": "entry", "original": "Text", "stage": 0}], 0, context, 1)
    release = threading.Event()
    worker = ApiWorker(lambda: release.wait(3), route_http_errors=False)
    dialog._workers.append(worker)
    finished = []
    dialog.finished.connect(finished.append)
    worker.start()
    dialog.show()
    _APP.processEvents()
    heartbeat = []
    expected = 23 if exit_method == "done" else int(exit_method == "accept")
    try:
        before = time.monotonic()
        if exit_method == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        elif exit_method == "done":
            dialog.done(expected)
        else:
            getattr(dialog, exit_method)()
        assert time.monotonic() - before < 0.5
        assert dialog.isVisible()
        assert worker.isRunning()
        assert not finished
        assert dialog._lifecycle._close_pending
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        _until(lambda: bool(heartbeat))
        # Repeated exit requests must not replace the original result.
        dialog.done(99)
        release.set()
        _until(lambda: bool(finished))
        assert finished == [expected]
        assert dialog.result() == expected
        assert not dialog.isVisible()
        assert not dialog._lifecycle._close_pending
        assert dialog._lifecycle._close_progress is None
    finally:
        release.set()
        assert worker.wait(3000)
        _APP.processEvents()
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("selected", [False, True])
def test_navigation_delegate_paints_real_text_with_brush_colors(selected, monkeypatch):
    original, key, highlighted = QColor("#d02020"), QColor("#2040d0"), QColor("#209040")
    palette = QPalette(_APP.palette())
    palette.setColor(QPalette.ColorRole.Text, original)
    palette.setColor(QPalette.ColorRole.PlaceholderText, key)
    palette.setColor(QPalette.ColorRole.HighlightedText, highlighted)
    monkeypatch.setattr(QApplication, "palette", staticmethod(lambda: palette))
    delegate = NavItemDelegate()
    model = QStandardItemModel()
    item = QStandardItem("Original text")
    item.setData("ENTRY KEY", _KEY_ROLE)
    model.appendRow(item)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 60)
    option.font = QFont("Arial", 12)
    option.state = QStyle.StateFlag.State_Enabled
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    image = QImage(300, 60, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, model.index(0, 0))
    finally:
        painter.end()
    pixels = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
    expected = (highlighted,) if selected else (original, key)
    assert all(color.rgba() in pixels for color in expected)


def test_close_also_waits_for_sync_worker_started_by_first_result():
    context = _Context()
    dialog = StringDetailDialog([], 0, context, 1)
    first_release, second_release = threading.Event(), threading.Event()
    first = ApiWorker(lambda: first_release.wait(3), route_http_errors=False)
    second = ApiWorker(lambda: second_release.wait(3), route_http_errors=False)
    dialog._workers.append(first)

    def start_sync(_result):
        dialog._workers.append(second)
        second.start()

    first.result.connect(start_sync)
    first.start()
    dialog.show()
    try:
        dialog.reject()
        first_release.set()
        _until(second.isRunning)
        assert dialog.isVisible()
        assert dialog._lifecycle._close_pending
        second_release.set()
        _until(lambda: not dialog.isVisible())
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        first_release.set()
        second_release.set()
        assert first.wait(3000)
        _APP.processEvents()
        assert second.wait(3000)
        _APP.processEvents()
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("start_followup", [False, True])
def test_close_waits_for_queued_results_after_native_thread_has_stopped(start_followup):
    context = _Context()
    dialog = StringDetailDialog([], 0, context, 1)
    dialog.show()
    _APP.processEvents()
    second_release = threading.Event()
    first = ApiWorker(lambda: "saved", route_http_errors=False)
    second = ApiWorker(lambda: second_release.wait(3), route_http_errors=False)
    dialog._workers.append(first)
    deliveries = []
    closed_states = []

    def receive_result(value):
        deliveries.append((value, dialog.isVisible()))
        dialog._modified = True
        if start_followup:
            dialog._workers.append(second)
            second.start()

    first.result.connect(receive_result)
    dialog.finished.connect(lambda _result: closed_states.append(dialog.was_modified()))
    first.start()
    try:
        # Intentionally join without dispatching GUI events: result and finished
        # are queued even though QThread.isRunning() is already false.
        assert first.wait(3000)
        assert not first.isRunning()
        assert not deliveries
        assert get_api_worker_registry().is_pending(first)
        dialog.reject()
        assert dialog.isVisible()
        assert not closed_states
        _until(lambda: bool(deliveries))
        assert deliveries == [("saved", True)]
        if start_followup:
            assert second.isRunning()
            assert dialog.isVisible()
            second_release.set()
        _until(lambda: not dialog.isVisible())
        assert closed_states == [True]
        assert not dialog._lifecycle._close_timer.isActive()
    finally:
        second_release.set()
        assert first.wait(3000)
        _APP.processEvents()
        assert second.wait(3000)
        _APP.processEvents()
        dialog.close()
        dialog.deleteLater()


def test_dialog_close_skips_deleted_workers_still_in_legacy_list():
    context = _Context()
    dialog = StringDetailDialog([], 0, context, 1)
    worker = ApiWorker(lambda: None, route_http_errors=False)
    dialog._workers.append(worker)
    worker.start()
    assert worker.wait(3000)
    _until(lambda: not get_api_worker_registry().is_pending(worker))
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)
    assert sip.isdeleted(worker)
    dialog.show()
    dialog.reject()
    assert not dialog.isVisible()
    dialog.deleteLater()
