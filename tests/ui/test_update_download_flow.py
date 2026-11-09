"""The unified update window never installs or retries before the worker is settled."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMainWindow
import pytest

from transbridge.application.update_service import ReleaseInfo
from transbridge.ui.shell.update_controller import UpdateController
from transbridge.ui.shell.update_dialog import UpdateDialog, UpdateState

_APP = QApplication.instance() or QApplication([])


class ControlledWorker(QObject):
    progress = pyqtSignal(int, int, str)
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, *_args, **_kwargs):
        super().__init__()

    def start(self):
        pass


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setattr("transbridge.ui.shell.update_controller.ApiWorker", ControlledWorker)
    monkeypatch.setattr("transbridge.ui.shell.update_controller.sys.frozen", True, raising=False)
    host = QMainWindow()
    host.close_pending = False
    controller = UpdateController(host)
    release = ReleaseInfo("0.2.1.10", "## 更新日志\n\n- 更新内容", "", "", "", 1000)
    controller._on_check_result(release, manual=False)
    assert controller._dialog is None
    controller.check_manually()
    yield host, controller, controller._dialog
    controller.cancel_installation()
    controller._dialog.close()
    QMainWindow.close(host)
    host.deleteLater()
    _APP.processEvents()


def test_known_release_opens_one_window_and_retains_notes_while_downloading(flow):
    host, controller, dialog = flow
    assert dialog.version_label.text() == "v0.2.1.10"
    controller.check_manually()
    assert host.findChildren(UpdateDialog) == [dialog]
    assert controller._worker is None
    dialog.primary_button.click()
    worker = controller._worker
    assert worker is not None
    worker.progress.emit(450, 1000, "")
    assert dialog.progress.value() == 450
    assert "更新内容" in dialog.notes.toPlainText()
    dialog.primary_button.click()
    assert controller._worker is worker
    worker.error.emit("网络连接中断")
    assert dialog.state == UpdateState.ERROR
    assert dialog.status_detail.text() == "网络连接中断"
    assert not dialog.primary_button.isEnabled()
    worker.finished.emit()
    dialog.primary_button.click()
    assert controller._worker is not None and controller._worker is not worker
    assert dialog.state == UpdateState.DOWNLOADING


def test_cancelled_download_ignores_late_result_then_allows_retry(flow):
    _host, controller, dialog = flow
    dialog.primary_button.click()
    worker = controller._worker
    dialog.secondary_button.click()
    assert controller._cancel_download.is_set()
    worker.result.emit(Path("unused-installer.exe"))
    assert controller._downloaded_installer is None
    worker.finished.emit()
    assert dialog.state == UpdateState.CANCELLED
    dialog.primary_button.click()
    assert not controller._cancel_download.is_set()
    assert controller._worker is not worker


def test_download_completion_waits_for_install_click_and_cancelled_exit_restores_window(flow, monkeypatch):
    host, controller, dialog = flow
    close_requests = []
    monkeypatch.setattr(host, "close", lambda: close_requests.append(True))
    dialog.primary_button.click()
    worker = controller._worker
    installer = Path("verified-installer.exe")
    worker.result.emit(installer)
    assert dialog.state == UpdateState.READY
    assert controller._pending_installer is None and close_requests == []
    dialog.primary_button.click()
    assert close_requests == []
    worker.finished.emit()
    dialog.secondary_button.click()
    assert not dialog.isVisible()
    controller.check_manually()
    assert controller._dialog is dialog and dialog.state == UpdateState.READY
    dialog.primary_button.click()
    assert close_requests == [True]
    assert controller._pending_installer == installer
    assert controller._installation_scheduled
    assert not dialog.isVisible()
    controller.cancel_installation()
    assert controller._pending_installer is None
    assert not controller._installation_scheduled
    assert dialog.isVisible() and dialog.state == UpdateState.READY


def test_close_during_download_cancels_without_reopening_on_completion(flow):
    _host, controller, dialog = flow
    dialog.primary_button.click()
    worker = controller._worker
    dialog.close()
    assert controller._cancel_download.is_set()
    worker.error.emit("cancelled")
    worker.finished.emit()
    assert not dialog.isVisible()
    controller.check_manually()
    assert dialog.isVisible() and dialog.state == UpdateState.CANCELLED


def test_main_window_close_cancels_transfer_and_worker_without_result_recovers(flow):
    _host, controller, dialog = flow
    dialog.primary_button.click()
    controller.prepare_for_close()
    assert controller._cancel_download.is_set()
    assert not dialog.isVisible()
    controller._worker.finished.emit()
    assert dialog.state == UpdateState.CANCELLED
    dialog.primary_button.click()
    controller._worker.finished.emit()
    assert dialog.state == UpdateState.ERROR
    assert dialog.primary_button.isEnabled()


def test_other_main_window_prevents_install_but_keeps_ready_package(flow, monkeypatch):
    host, controller, dialog = flow
    peer = QMainWindow()
    peer.show()
    closed = []
    monkeypatch.setattr(host, "close", lambda: closed.append(True))
    try:
        dialog.primary_button.click()
        worker = controller._worker
        worker.result.emit(Path("verified-installer.exe"))
        worker.finished.emit()
        dialog.primary_button.click()
        assert not closed and not controller._installation_scheduled
        assert dialog.state == UpdateState.READY
        assert "其他 TransBridge 主窗口" in dialog.status_detail.text()
    finally:
        peer.close()
