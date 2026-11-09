"""Release metadata, complete notes and actions across update dialog states."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.update_service import ReleaseInfo
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.shell.update_dialog import UpdateDialog, UpdateState

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(params=[ThemeMode.LIGHT, ThemeMode.DARK])
def dialog(request):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=request.param, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(_APP, create_builtin_registry(), preferences)
    service.start()
    release = ReleaseInfo(
        "0.2.1.10",
        "## 本次更新\n\n- 改进界面\n\n" + "完整更新内容\n\n" * 600 + "日志末尾标记",
        "https://github.com/BuMing119/TransBridge/releases/tag/v0.2.1.10",
        "",
        "",
        128 * 1024 * 1024,
    )
    widget = UpdateDialog(release, "0.2.1.9")
    widget.show()
    _APP.processEvents()
    yield widget
    widget.set_transfer_active(False)
    widget.show_cancelled()
    widget.close()
    widget.deleteLater()
    _APP.processEvents()
    service.close()


def test_versions_and_full_markdown_remain_visible_during_download(dialog):
    assert dialog.version_label.text() == "v0.2.1.10"
    assert dialog.current_label.text() == "当前版本 v0.2.1.9"
    assert dialog.notes.isReadOnly()
    text = dialog.notes.toPlainText()
    assert "本次更新" in text and "## 本次更新" not in text
    assert text.endswith("日志末尾标记") and len(text) > 3000
    assert dialog.notes.verticalScrollBar().maximum() > 0
    dialog.set_transfer_active(True)
    dialog.show_downloading()
    dialog.set_download_progress(64 * 1024 * 1024, 128 * 1024 * 1024)
    assert dialog.progress.value() == 500
    assert "50% · 64.0 MB / 128.0 MB" == dialog.status_detail.text()
    assert dialog.notes.toPlainText() == text
    assert dialog.version_label.isVisible() and dialog.notes.isVisible()
    assert not dialog.primary_button.isEnabled()
    assert dialog.secondary_button.text() == "取消下载"
    dialog.set_download_progress(128 * 1024 * 1024, 128 * 1024 * 1024)
    assert dialog.status_title.text() == "正在校验安装包…"
    assert not dialog.primary_button.isEnabled()


def test_install_requires_explicit_action_after_worker_finishes(dialog):
    installed = []
    dialog.install_requested.connect(lambda: installed.append(True))
    dialog.set_transfer_active(True)
    dialog.show_downloading()
    dialog.show_ready()
    assert dialog.state == UpdateState.READY
    dialog.primary_button.click()
    assert not installed
    dialog.set_transfer_active(False)
    assert dialog.primary_button.text() == "保存并安装"
    assert "保存工作并退出" in dialog.install_hint.text()
    dialog.primary_button.click()
    assert installed == [True]


@pytest.mark.parametrize("close_action", ["cancel", "escape", "close"])
def test_cancel_and_window_close_request_cancellation_once(dialog, close_action):
    cancelled = []
    dialog.cancel_requested.connect(lambda: cancelled.append(True))
    dialog.set_transfer_active(True)
    dialog.show_downloading()
    if close_action == "cancel":
        dialog.secondary_button.click()
    elif close_action == "escape":
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()
    assert cancelled == [True]
    assert dialog.state == UpdateState.CANCELLING
    assert not dialog.primary_button.isEnabled()
    dialog.set_download_progress(100, 100)
    assert dialog.status_title.text() == "正在取消下载…"
    dialog.set_transfer_active(False)
    dialog.show_cancelled()
    assert dialog.primary_button.isEnabled()
    assert dialog.primary_button.text() == "重新下载"


def test_release_links_only_open_web_urls_and_images_are_not_loaded(dialog, monkeypatch):
    opened = []
    monkeypatch.setattr("transbridge.ui.shell.update_dialog.QDesktopServices.openUrl", lambda url: opened.append(url))
    for link in ("file:///C:/private.txt", "javascript:alert(1)", "data:text/html,test", "relative/path"):
        dialog.notes.anchorClicked.emit(QUrl(link))
    assert not opened
    assert dialog.notes.loadResource(2, QUrl("file:///C:/private.png")) is None
    dialog.release_link.click()
    assert opened == [QUrl(dialog.release.html_url)]


def test_empty_notes_and_source_run_explain_available_actions():
    release = ReleaseInfo("0.2.1.10", " \n ", "", "", "", 1024)
    dialog = UpdateDialog(release, "0.2.1.9", can_install=False)
    assert dialog.notes.toPlainText() == "本次发布未提供更新说明。"
    assert not dialog.primary_button.isEnabled()
    assert "源码运行" in dialog.install_hint.text()
    dialog.close()


def test_minimum_size_keeps_notes_and_actions_inside_window(dialog):
    dialog.resize(520, 500)
    _APP.processEvents()
    for widget in (dialog.version_label, dialog.notes, dialog.primary_button, dialog.secondary_button):
        assert dialog.rect().contains(widget.geometry())
    assert dialog.notes.height() >= 140
