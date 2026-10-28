from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox, QPushButton, QWidget

from transbridge.ui.shell.close_input_guard import CloseInputGuard
from transbridge.ui.shell.close_progress import CloseProgress

from . import test_ai_task_shutdown

qapp = test_ai_task_shutdown.qapp
shutdown_host = test_ai_task_shutdown.shutdown_host


def test_cancel_exit_ignores_old_save_callback_during_next_attempt(shutdown_host):
    host, registry, lifecycle, _scheduled, _warning = shutdown_host
    lifecycle.close_event(QCloseEvent())
    old_finished = host.save_current_project_async.call_args.kwargs["on_finished"]
    lifecycle.cancel_close()
    assert not host.close_pending
    registry.resume.assert_called_once()
    lifecycle.close_event(QCloseEvent())
    new_finished = host.save_current_project_async.call_args.kwargs["on_finished"]
    old_finished(True)
    host.close.assert_not_called()
    new_finished(True)
    host.close.assert_called_once()


def test_cancel_exit_invalidates_old_poll_callbacks(shutdown_host):
    host, registry, lifecycle, scheduled, _warning = shutdown_host
    registry.busy = True
    lifecycle.close_event(QCloseEvent())
    old_poll = scheduled.pop()[1]
    lifecycle.cancel_close()
    registry.busy = False
    lifecycle.close_event(QCloseEvent())
    host.save_current_project_async.assert_called_once()
    old_poll()
    host.save_current_project_async.assert_called_once()
    lifecycle.cancel_close()


def test_nested_draft_confirmation_cannot_finish_a_new_close_attempt(shutdown_host):
    host, _registry, lifecycle, _scheduled, _warning = shutdown_host
    lifecycle.close_event(QCloseEvent())
    old_finished = host.save_current_project_async.call_args.kwargs["on_finished"]

    def reopen():
        lifecycle.cancel_close()
        host.workbench.confirm_inline_edit_close.side_effect = None
        lifecycle.close_event(QCloseEvent())
        return True

    host.workbench.confirm_inline_edit_close.side_effect = reopen
    old_finished(True)
    assert host.save_current_project_async.call_count == 2
    host.close.assert_not_called()
    assert host.close_pending and not host.close_ready
    host.save_current_project_async.call_args.kwargs["on_finished"](True)
    host.close.assert_called_once()


def test_nested_record_answer_cannot_authorize_discard_for_a_new_attempt(shutdown_host, monkeypatch):
    host, registry, lifecycle, _scheduled, _warning = shutdown_host
    lifecycle.close_event(QCloseEvent())
    old_finished = host.save_current_project_async.call_args.kwargs["on_finished"]
    registry.record_errors = ("disk full",)

    def reopen(*_args):
        lifecycle.cancel_close()
        registry.record_errors = ()
        lifecycle.close_event(QCloseEvent())
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "question", reopen)
    old_finished(True)
    assert not lifecycle._discard_ai_record_errors
    host.close.assert_not_called()
    assert host.close_pending
    lifecycle.cancel_close()


def test_nested_backup_destination_cannot_leak_into_a_new_attempt(shutdown_host, tmp_path):
    host, _registry, lifecycle, scheduled, _warning = shutdown_host
    lifecycle.close_event(QCloseEvent())
    old_finished = host.save_current_project_async.call_args.kwargs["on_finished"]

    def reopen(_host):
        lifecycle.cancel_close()
        lifecycle.close_event(QCloseEvent())
        return tmp_path / "old-attempt-backup.json"

    host.choose_recovery.side_effect = reopen
    old_finished(False)
    assert lifecycle._recovery_destination is None
    assert not scheduled
    host.close.assert_not_called()
    assert host.close_pending
    lifecycle.cancel_close()


def test_return_button_stays_enabled_while_other_windows_are_disabled(qapp):
    host = QWidget()
    host.show()
    cancelled = []
    progress = CloseProgress(host, lambda: cancelled.append(True))
    guard = CloseInputGuard()
    try:
        progress.show()
        guard.suspend()
        assert not host.isEnabled()
        assert progress._dialog.isEnabled()
        progress._dialog.findChild(QPushButton).click()
        assert cancelled == [True]
    finally:
        guard.resume()
        progress.hide()
        host.close()
