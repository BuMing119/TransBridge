from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
import pytest

from transbridge.ui import context as context_module
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.shell.status_presenter import StatusPresenter

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def notifications():
    window = QMainWindow()
    window.setCentralWidget(QWidget())
    window.resize(900, 650)
    window.show()
    presenter = StatusPresenter(window, SimpleNamespace())
    yield presenter
    presenter.close()
    window.close()


def test_error_persists_and_full_details_can_be_read_and_copied(notifications):
    message = "解析失败：测试插件.esp\n" + "完整原因 " * 80
    notifications.show_message(message, error=True)
    toast = notifications._message_toast
    assert not toast._timer.isActive()
    assert not toast.isHidden()
    toast.details_button.click()
    assert not toast.details.isHidden()
    assert toast.details.toPlainText() == message
    toast.copy_button.click()
    assert QApplication.clipboard().text() == message
    notifications.show_message("另一个任务完成")
    assert toast.details.toPlainText() == message
    assert not toast._timer.isActive()
    toast.close_button.click()
    assert toast.isHidden()
    notifications.show_message("普通提示")
    assert toast._timer.isActive()
    assert toast.actions.isHidden()


def test_recent_failures_are_bounded_and_each_can_be_recovered(notifications):
    recovered = []
    for index in range(22):
        notifications.show_message(
            f"失败 {index}",
            error=True,
            retry=lambda value=index: recovered.append(value),
            retry_label="重新选择文件…",
        )
    toast = notifications._message_toast
    assert toast.history.count() == 20
    toast.history.setCurrentIndex(0)
    assert toast.details.toPlainText() == "失败 2"
    assert toast.retry_button.text() == "重新选择文件…"
    toast.retry_button.click()
    assert recovered == [2]
    assert toast.history.count() == 19
    assert toast.details.toPlainText() == "失败 3"
    assert not toast._timer.isActive()


def test_recovery_exception_remains_inspectable(notifications, caplog):
    def fail():
        raise RuntimeError("recovery failed")

    notifications.show_message("初次失败", error=True, retry=fail)
    toast = notifications._message_toast
    toast.retry_button.click()
    assert "recovery failed" in toast.details.toPlainText()
    assert not toast._timer.isActive()
    assert "Notification recovery failed" in caplog.text


def test_notification_reflows_with_parent_and_shutdown_stops_timer(notifications):
    notifications.show_message("提示")
    toast = notifications._message_toast
    notifications._window.resize(420, 400)
    QApplication.processEvents()
    assert toast.geometry().right() <= toast.parentWidget().width()
    notifications.close()
    assert not toast._timer.isActive()
    assert toast.isHidden()


@pytest.mark.parametrize("kind", ["eet", "plugin", "batch"])
def test_parser_failure_retains_source_and_offers_user_confirmed_recovery(notifications, monkeypatch, kind):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    progress = []
    host = SimpleNamespace(
        context=context_module.AppContext(),
        workers=[],
        show_message=notifications.show_message,
        workbench=SimpleNamespace(
            show_step2_progress=lambda *_: None,
            hide_step2_progress=lambda: None,
            update_step2_progress=lambda *_: None,
            set_step2_parsing=progress.append,
        ),
    )
    coordinator = ParseCoordinator(host)
    opened = []
    monkeypatch.setattr(coordinator, "parse_plugin", lambda: opened.append("configuration"))
    missing = "missing-file-for-ui-notification-test"
    config = SimpleNamespace(
        eet_path=f"{missing}.xml" if kind == "eet" else None,
        esp_paths=[f"{missing}.esp", f"{missing}-second.esp"],
        skip_empty=True,
        xt_path=None,
        tp_path=None,
        strings_dir=None,
        strings_lang="english",
    )
    run = {
        "eet": coordinator._run_parse_eet,
        "plugin": coordinator._run_parse_esp,
        "batch": coordinator._run_batch_parse_esp,
    }[kind]
    run(config)
    deadline = time.monotonic() + 5
    while progress[-1] and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    for worker in host.workers:
        assert worker.wait(5000)
    _APP.processEvents()
    assert progress[-1] is False
    toast = notifications._message_toast
    assert not toast._timer.isActive()
    assert missing in toast.details.toPlainText()
    if kind == "batch":
        assert f"{missing}-second.esp" in toast.details.toPlainText()
    assert opened == []
    toast.retry_button.click()
    assert opened == ["configuration"]
