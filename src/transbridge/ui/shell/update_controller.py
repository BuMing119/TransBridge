# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Desktop update UI and the handoff to the existing installer."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import sys
from threading import Event

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from transbridge import __version__
from transbridge.application.update_service import ReleaseInfo, check_latest, download_release
from transbridge.ui.workers import ApiWorker

from .update_dialog import UpdateDialog, UpdateState
from .update_handoff import launch_after_exit

logger = logging.getLogger(__name__)


class UpdateController(QObject):
    """Keep update requests out of the navigation rail and main window."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self._host = host
        self._navigation = None
        self._worker: ApiWorker | None = None
        self._release: ReleaseInfo | None = None
        self._dialog: UpdateDialog | None = None
        self._download_active = False
        self._downloaded_installer: Path | None = None
        self._cancel_download = Event()
        self._pending_installer: Path | None = None
        self._installation_scheduled = False
        self._checked_once = False
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.check_automatically)

    def bind_navigation(self, navigation) -> None:
        self._navigation = navigation

    def start(self) -> None:
        """Check packaged builds after the first screen has rendered."""
        if getattr(sys, "frozen", False):
            QTimer.singleShot(0, self._show_previous_install_error)
            self._auto_timer.start(3000)

    @staticmethod
    def _update_directory() -> Path:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TransBridge" / "updates"

    def _show_previous_install_error(self) -> None:
        error_file = self._update_directory() / "last-update-error.txt"
        if not error_file.is_file():
            return
        try:
            message = (
                error_file.read_text(encoding="utf-8-sig")[:1000]
                if error_file.stat().st_size <= 4096
                else "安装程序未能完成。"
            )
            error_file.unlink()
        except OSError:
            logger.exception("Could not read the previous update result")
            return
        QMessageBox.warning(
            self._host,
            "上次更新未完成",
            f"{message or '安装程序未能完成。'}\n\n可重新点击“检查更新”，或从更新缓存目录手动运行安装包。",
        )

    def check_automatically(self) -> None:
        if not self._checked_once:
            self._check(manual=False)

    def check_manually(self) -> None:
        if self._host.close_pending or self._installation_scheduled:
            return
        if self._dialog is not None:
            self._show_update_dialog()
        elif self._release is not None:
            self._offer_release(self._release)
        else:
            self._check(manual=True)

    def _check(self, *, manual: bool) -> None:
        if self._host.close_pending or not self._host.isVisible():
            return
        if self._worker is not None or self._installation_scheduled:
            if manual:
                self._show_info("更新检查或下载正在进行，请稍候。")
            return
        self._checked_once = True
        worker = ApiWorker(lambda: check_latest(__version__), route_http_errors=False)
        self._worker = worker
        worker.result.connect(lambda release: self._on_check_result(release, manual=manual))
        worker.error.connect(lambda message: self._on_check_error(message, manual=manual))
        worker.finished.connect(lambda: self._release_worker(worker))
        if self._navigation is not None:
            self._navigation.set_update_checking(True)
        worker.start()

    def _on_check_result(self, release: ReleaseInfo | None, *, manual: bool) -> None:
        self._release = release
        if self._navigation is not None:
            self._navigation.set_update_checking(False)
            self._navigation.set_update_available(release is not None, None if release is None else release.version)
        if manual and not self._host.close_pending:
            if release is None:
                self._show_info("当前已是最新版本，或尚无可用的正式安装包。")
            else:
                QTimer.singleShot(0, lambda: self._offer_release(release))

    def _on_check_error(self, message: str, *, manual: bool) -> None:
        logger.warning("Update check failed: %s", message)
        if self._navigation is not None:
            self._navigation.set_update_checking(False)
        if manual and not self._host.close_pending:
            QMessageBox.warning(self._host, "检查更新失败", message)

    def _offer_release(self, release: ReleaseInfo) -> None:
        if self._host.close_pending:
            return
        if self._dialog is None or self._dialog.release != release:
            if self._dialog is not None:
                self._dialog.close()
                self._dialog.deleteLater()
            self._release = release
            self._downloaded_installer = None
            self._dialog = UpdateDialog(release, __version__, self._host, can_install=getattr(sys, "frozen", False))
            self._dialog.download_requested.connect(lambda: self._download(release))
            self._dialog.cancel_requested.connect(self._cancel_download.set)
            self._dialog.install_requested.connect(self._install_downloaded)
        self._show_update_dialog()

    def _show_update_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()

    def _download(self, release: ReleaseInfo) -> None:
        if self._host.close_pending or not getattr(sys, "frozen", False):
            return
        if self._worker is not None:
            self._show_info("更新检查或下载正在进行，请稍候。")
            return
        if self._dialog is None:
            self._offer_release(release)
        self._cancel_download.clear()
        self._downloaded_installer = None
        self._download_active = True
        self._dialog.set_transfer_active(True)
        self._dialog.show_downloading()

        def fetch() -> Path:
            return download_release(
                release,
                self._update_directory(),
                progress=lambda done, total: worker.progress.emit(done, total, ""),
                cancelled=self._cancel_download.is_set,
            )

        worker = ApiWorker(fetch, route_http_errors=False)
        self._worker = worker
        worker.progress.connect(self._on_download_progress)
        worker.result.connect(self._on_download_ready)
        worker.error.connect(self._on_download_error)
        worker.finished.connect(lambda: self._release_worker(worker))
        worker.start()

    def _on_download_progress(self, done: int, total: int, _message: str) -> None:
        if self._dialog is not None:
            self._dialog.set_download_progress(done, total)

    def _on_download_error(self, message: str) -> None:
        if self._dialog is not None:
            if self._cancel_download.is_set():
                self._dialog.show_cancelled()
            else:
                self._dialog.show_error(message)

    def _on_download_ready(self, installer: Path) -> None:
        if self._cancel_download.is_set() or self._host.close_pending:
            return
        self._downloaded_installer = installer
        if self._dialog is not None:
            self._dialog.show_ready()

    def _install_downloaded(self) -> None:
        installer = self._downloaded_installer
        if installer is None or self._worker is not None or self._host.close_pending or self._installation_scheduled:
            return
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Update installation requires a QApplication")
        other_windows = [
            widget
            for widget in app.topLevelWidgets()
            if widget is not self._host and isinstance(widget, type(self._host)) and widget.isVisible()
        ]
        if other_windows:
            if self._dialog is not None:
                self._dialog.show_ready("请先关闭其他 TransBridge 主窗口，再从本窗口启动安装。")
            return
        self._pending_installer = installer
        app.aboutToQuit.connect(self._launch_pending_installer)
        self._installation_scheduled = True
        if self._dialog is not None:
            self._dialog.show_installing()
            self._dialog.hide()
        self._host.close()

    def cancel_installation(self) -> None:
        """A cancelled project close must cancel the pending installer too."""
        self._pending_installer = None
        if self._installation_scheduled:
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.disconnect(self._launch_pending_installer)
            self._installation_scheduled = False
            if self._dialog is not None and self._downloaded_installer is not None:
                self._dialog.show_ready("已取消退出，安装包已保留，可稍后安装。")
                self._show_update_dialog()
        if getattr(sys, "frozen", False) and not self._checked_once:
            self._auto_timer.start(3000)

    def prepare_for_close(self) -> None:
        """Stop an in-flight update download before normal close waits for workers."""
        self._auto_timer.stop()
        if self._download_active:
            self._cancel_download.set()
        if self._dialog is not None:
            self._dialog.hide()

    def _launch_pending_installer(self) -> None:
        installer = self._pending_installer
        self._pending_installer = None
        if installer is None:
            return
        try:
            release = self._release
            if release is None or not installer.is_file():
                raise OSError("The verified installer is no longer available")
            with installer.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest().lower() != release.sha256.lower():
                    raise OSError("The downloaded installer changed after verification")
            # Qt has accepted close, but app.py still owns runtime cleanup.
            launch_after_exit(installer, sha256=release.sha256, previous_pid=os.getpid())
        except OSError:
            logger.exception("Could not launch verified update installer: %s", installer)
            try:
                (installer.parent / "last-update-error.txt").write_text(
                    "无法启动更新安装程序；请重新检查更新或手动运行已下载的安装包。", encoding="utf-8"
                )
            except OSError:
                logger.exception("Could not record update launch failure")

    def _release_worker(self, worker: ApiWorker) -> None:
        if self._worker is worker:
            self._worker = None
            if self._download_active:
                self._download_active = False
                if self._dialog is not None:
                    self._dialog.set_transfer_active(False)
                    if self._cancel_download.is_set():
                        self._downloaded_installer = None
                        self._dialog.show_cancelled()
                    elif self._dialog.state in {UpdateState.DOWNLOADING, UpdateState.CANCELLING}:
                        self._dialog.show_error("下载未完成，请重试。")
            if self._navigation is not None:
                self._navigation.set_update_checking(False)
        worker.deleteLater()

    def _show_info(self, message: str) -> None:
        QMessageBox.information(self._host, "检查更新", message)
