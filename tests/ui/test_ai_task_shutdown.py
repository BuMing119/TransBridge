from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from transbridge.ui.shell import window_lifecycle
from transbridge.ui.shell.window_lifecycle import WindowLifecycle


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shutdown_host(qapp, monkeypatch):
    host = QObject()
    host.close_pending = host.close_ready = False
    host.project_open_worker = host.foreground_worker = None
    host.context = SimpleNamespace(dirty=False, workspace=Mock(), close_projection=Mock())
    registry = SimpleNamespace(busy=False, record_errors=(), shutting_down=False, dispose=Mock())
    registry.shutdown = Mock(side_effect=lambda: setattr(registry, "shutting_down", True))
    registry.resume = Mock(side_effect=lambda: setattr(registry, "shutting_down", False))
    host.workbench = Mock(ai_tasks=registry)
    host.save_current_project_async = Mock(return_value=True)
    host.project_coordinator = Mock()
    host.tool_windows = Mock()
    host.status_presenter = Mock()
    host.saveGeometry = Mock(return_value=b"geometry")
    host.saveState = Mock(return_value=b"state")
    host.close = Mock()
    settings = Mock()
    monkeypatch.setattr(window_lifecycle, "QSettings", lambda *_: settings)
    scheduled = []
    monkeypatch.setattr(
        window_lifecycle.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback))
    )
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    lifecycle = WindowLifecycle(host)
    yield host, registry, lifecycle, scheduled, warning
    lifecycle.auto_saver.stop()


def test_close_waits_for_ai_tasks_before_project_save_then_disposes(shutdown_host):
    host, registry, lifecycle, scheduled, _ = shutdown_host
    registry.busy = True
    event = QCloseEvent()

    assert not lifecycle.close_event(event)
    assert not event.isAccepted()
    registry.shutdown.assert_called_once()
    assert registry.shutting_down and host.close_pending
    host.workbench.setEnabled.assert_called_with(False)
    host.save_current_project_async.assert_not_called()
    assert len(scheduled) == 1 and scheduled[0][0] == 100
    host.close.assert_not_called()

    registry.busy = False
    scheduled.pop()[1]()
    host.save_current_project_async.assert_called_once()
    callback = host.save_current_project_async.call_args.kwargs["on_finished"]
    callback(True)

    registry.dispose.assert_called_once()
    host.tool_windows.dispose.assert_called_once_with(wait_for_worker=False)
    host.context.close_projection.assert_called_once()
    host.context.workspace.save.assert_called_once()
    host.close.assert_called_once()
    assert host.close_ready
    assert lifecycle.close_event(QCloseEvent())


def test_project_save_busy_reschedules_without_closing(shutdown_host):
    host, registry, lifecycle, scheduled, _ = shutdown_host
    host.save_current_project_async.side_effect = [False, True]

    lifecycle.close_event(QCloseEvent())
    assert len(scheduled) == 1 and scheduled[0][0] == 0
    assert registry.shutting_down
    host.close.assert_not_called()
    scheduled.pop()[1]()
    assert host.save_current_project_async.call_count == 2
    assert not scheduled
    host.close.assert_not_called()


def test_project_save_observer_history_write_finishes_before_disposal(shutdown_host):
    host, registry, lifecycle, scheduled, _ = shutdown_host
    lifecycle.close_event(QCloseEvent())
    assert host.save_current_project_async.call_count == 1
    # Saving the project synchronously notifies AI sessions; their history
    # records are then written in another worker after the initial busy check.
    registry.busy = True
    host.save_current_project_async.call_args.kwargs["on_finished"](True)

    assert len(scheduled) == 1
    assert scheduled[0][0] > 0
    registry.dispose.assert_not_called()
    host.context.close_projection.assert_not_called()
    host.close.assert_not_called()
    assert host.close_pending and not host.close_ready

    registry.busy = False
    scheduled.pop()[1]()
    assert host.save_current_project_async.call_count == 1
    registry.dispose.assert_called_once()
    host.close.assert_called_once()
    assert host.close_ready


def test_post_save_history_failure_can_still_cancel_exit(shutdown_host, monkeypatch):
    host, registry, lifecycle, scheduled, _ = shutdown_host
    question = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", question)
    lifecycle.close_event(QCloseEvent())
    registry.busy = True
    host.save_current_project_async.call_args.kwargs["on_finished"](True)
    assert len(scheduled) == 1

    registry.busy = False
    registry.record_errors = ("post-save record failed",)
    scheduled.pop()[1]()
    question.assert_called_once()
    registry.resume.assert_called_once()
    assert not host.close_pending and not registry.shutting_down
    host.workbench.setEnabled.assert_called_with(True)
    registry.dispose.assert_not_called()
    host.close.assert_not_called()


@pytest.mark.parametrize("discard", [False, True])
def test_failed_task_record_can_abort_exit_or_be_explicitly_discarded(shutdown_host, monkeypatch, discard):
    host, registry, lifecycle, scheduled, _ = shutdown_host
    registry.record_errors = ("history disk full",)
    answer = QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel
    question = Mock(return_value=answer)
    monkeypatch.setattr(QMessageBox, "question", question)

    lifecycle.close_event(QCloseEvent())

    question.assert_called_once()
    assert question.call_args.args[-1] is QMessageBox.StandardButton.Cancel
    assert not scheduled
    host.close.assert_not_called()
    if discard:
        host.save_current_project_async.assert_called_once()
        assert host.close_pending and registry.shutting_down
        registry.resume.assert_not_called()
        host.save_current_project_async.call_args.kwargs["on_finished"](True)
        host.close.assert_called_once()
    else:
        host.save_current_project_async.assert_not_called()
        registry.resume.assert_called_once()
        assert not host.close_pending and not lifecycle._close_pending
        assert not registry.shutting_down and not lifecycle.auto_saver._stopped
        host.workbench.setEnabled.assert_called_with(True)
        host.workbench.hide_step2_progress.assert_called_once()
        registry.dispose.assert_not_called()


@pytest.mark.parametrize(("saved", "dirty"), [(False, False), (True, True)])
def test_failed_or_still_dirty_project_save_restores_application(shutdown_host, saved, dirty):
    host, registry, lifecycle, scheduled, warning = shutdown_host
    lifecycle.close_event(QCloseEvent())
    assert registry.shutting_down and host.close_pending
    host.context.dirty = dirty
    host.save_current_project_async.call_args.kwargs["on_finished"](saved)

    warning.assert_called_once()
    registry.resume.assert_called_once()
    assert not registry.shutting_down
    assert not host.close_pending and not host.close_ready
    assert not lifecycle._close_pending and not lifecycle.auto_saver._stopped
    host.workbench.setEnabled.assert_called_with(True)
    host.workbench.hide_step2_progress.assert_called_once()
    host.context.close_projection.assert_not_called()
    registry.dispose.assert_not_called()
    host.close.assert_not_called()
    assert not scheduled
