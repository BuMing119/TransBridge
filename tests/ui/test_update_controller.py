"""Desktop update behavior at the save/exit and installer boundary."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMainWindow
import pytest

from transbridge.application.update_service import ReleaseInfo
from transbridge.ui.shell.navigation_rail import NavigationRail
from transbridge.ui.shell.update_controller import UpdateController

_APP = QApplication.instance() or QApplication([])


class NavigationStub:
    def __init__(self) -> None:
        self.state = None
        self.checking = False

    def set_update_checking(self, checking: bool) -> None:
        self.checking = checking

    def set_update_available(self, available: bool, version: str | None = None) -> None:
        self.state = available, version


def _release(content: bytes) -> ReleaseInfo:
    return ReleaseInfo(
        version="0.2.1.10",
        notes="Release notes",
        html_url="https://github.com/BuMing119/TransBridge/releases/tag/v0.2.1.10",
        asset_url="https://github.com/BuMing119/TransBridge/releases/download/v0.2.1.10/"
        "TransBridge_v0.2.1.10_Setup.exe",
        sha256=sha256(content).hexdigest(),
        size=len(content),
    )


def test_new_release_updates_navigation_without_installing() -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    navigation = NavigationStub()
    controller.bind_navigation(navigation)
    release = _release(b"installer")

    controller._on_check_result(release, manual=False)

    assert navigation.state == (True, "0.2.1.10")
    assert controller._release is release
    assert controller._pending_installer is None
    host.close()


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("outcome", ["available", "latest", "error", "finished"])
def test_check_animation_tracks_worker_lifetime(monkeypatch, manual, outcome) -> None:
    class ControlledWorker(QObject):
        result = pyqtSignal(object)
        error = pyqtSignal(str)
        finished = pyqtSignal()

        def __init__(self, *_args, **_kwargs):
            super().__init__()

        def start(self):
            pass

    monkeypatch.setattr("transbridge.ui.shell.update_controller.ApiWorker", ControlledWorker)
    monkeypatch.setattr("transbridge.ui.shell.update_controller.QMessageBox.warning", lambda *_args: None)
    host = QMainWindow()
    host.close_pending = False
    navigation = NavigationRail(host)
    host.setCentralWidget(navigation)
    host.show()
    controller = UpdateController(host)
    controller.bind_navigation(navigation)
    monkeypatch.setattr(controller, "_show_info", lambda *_args: None)
    monkeypatch.setattr(controller, "_offer_release", lambda *_args: None)
    try:
        controller._check(manual=manual)
        worker = controller._worker
        button = navigation._update_button
        assert button.text() == "正在检查…"
        assert not button.isEnabled()
        # A second request cannot replace the worker or restart the animation.
        controller._check(manual=manual)
        assert controller._worker is worker
        if outcome == "available":
            worker.result.emit(_release(b"installer"))
        elif outcome == "latest":
            worker.result.emit(None)
        elif outcome == "error":
            worker.error.emit("offline")
        worker.finished.emit()
        assert controller._worker is None
        assert button.isEnabled()
        assert button.text() == ("发现新版本" if outcome == "available" else "检查更新")
        assert button._rotation.state() == button._rotation.State.Stopped
        _APP.processEvents()
    finally:
        host.close()


def test_closing_window_cancels_active_download() -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    controller._download_active = True

    controller.prepare_for_close()

    assert controller._cancel_download.is_set()
    host.close()


def test_cancelled_close_restores_pending_automatic_check(monkeypatch) -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    monkeypatch.setattr("transbridge.ui.shell.update_controller.sys.frozen", True, raising=False)
    controller.prepare_for_close()

    controller.cancel_installation()

    assert controller._auto_timer.isActive()
    controller._auto_timer.stop()
    host.close()


def test_failed_handoff_is_reported_on_next_start(tmp_path: Path, monkeypatch) -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    error_file = tmp_path / "last-update-error.txt"
    error_file.write_text("UAC cancelled", encoding="utf-8")
    monkeypatch.setattr(controller, "_update_directory", lambda: tmp_path)
    shown = []
    monkeypatch.setattr(
        "transbridge.ui.shell.update_controller.QMessageBox.warning",
        lambda *args: shown.append(args),
    )

    controller._show_previous_install_error()

    assert "UAC cancelled" in shown[0][2]
    assert not error_file.exists()
    host.close()


def test_cancelled_close_clears_pending_install(tmp_path: Path, monkeypatch) -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    installer = tmp_path / "TransBridge_v0.2.1.10_Setup.exe"
    installer.write_bytes(b"installer")
    controller._release = _release(b"installer")
    controller._pending_installer = installer
    controller._installation_scheduled = True
    _APP.aboutToQuit.connect(controller._launch_pending_installer)
    launched = []
    monkeypatch.setattr(
        "transbridge.ui.shell.update_controller.launch_after_exit",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    controller.cancel_installation()
    controller._launch_pending_installer()

    assert launched == []
    assert controller._pending_installer is None
    assert not controller._installation_scheduled
    host.close()


def test_handoff_receives_expected_digest_and_original_application(tmp_path: Path, monkeypatch) -> None:
    host = QMainWindow()
    controller = UpdateController(host)
    installer = tmp_path / "TransBridge_v0.2.1.10_Setup.exe"
    installer.write_bytes(b"installer")
    controller._release = _release(b"installer")
    controller._pending_installer = installer
    launched = []
    monkeypatch.setattr(
        "transbridge.ui.shell.update_controller.launch_after_exit",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    controller._launch_pending_installer()

    assert launched == [
        (
            (installer,),
            {"sha256": controller._release.sha256, "previous_pid": os.getpid(), "application": Path(sys.executable)},
        )
    ]
    host.close()


@pytest.mark.parametrize("missing", [False, True])
def test_handoff_failure_is_visible_and_saved(tmp_path: Path, monkeypatch, missing):
    host = QMainWindow()
    controller = UpdateController(host)
    installer = tmp_path / "Setup.exe"
    if not missing:
        installer.write_bytes(b"installer")
    controller._release = _release(b"installer")
    controller._pending_installer = installer

    def cannot_launch(*_args, **_kwargs):
        raise OSError("PowerShell unavailable")

    monkeypatch.setattr("transbridge.ui.shell.update_controller.launch_after_exit", cannot_launch)
    shown = []
    monkeypatch.setattr("transbridge.ui.shell.update_controller.QMessageBox.warning", lambda *args: shown.append(args))
    controller._launch_pending_installer()
    assert len(shown) == 1
    assert "无法启动更新安装程序" in shown[0][2]
    assert (tmp_path / "last-update-error.txt").read_text(encoding="utf-8") == shown[0][2]
    assert controller._pending_installer is None
    host.close()
