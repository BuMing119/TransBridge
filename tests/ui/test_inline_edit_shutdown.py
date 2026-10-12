from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.ui import test_ai_task_shutdown, test_inline_edit_recovery
from transbridge.ui.shell import window_lifecycle
from transbridge.ui.workbench.translation_table_columns import COL_TRANSLATION

qapp = test_ai_task_shutdown.qapp
shutdown_host = test_ai_task_shutdown.shutdown_host
editing = test_inline_edit_recovery.editing


@pytest.fixture
def guarded_shutdown(editing, shutdown_host):
    widget, context, entry, response = editing
    host, registry, lifecycle, scheduled, _warning = shutdown_host
    host.workbench.confirm_inline_edit_close = widget.confirm_inline_edit_close
    yield widget, context, entry, response, host, registry, lifecycle, scheduled
    lifecycle._input_guard.resume()


def _assert_exit_cancelled(host, registry, lifecycle):
    assert not host.close_pending and not host.close_ready
    assert not lifecycle._close_pending and not lifecycle.auto_saver._stopped
    assert not registry.shutting_down
    assert not lifecycle._input_guard._windows
    host.workbench.setEnabled.assert_called_with(True)
    host.workbench.hide_step2_progress.assert_called()
    host.context.close_projection.assert_not_called()
    registry.dispose.assert_not_called()
    host.close.assert_not_called()


@pytest.mark.parametrize("discard", [False, True])
def test_failed_inline_draft_requires_explicit_discard_before_close(guarded_shutdown, monkeypatch, discard):
    widget, context, entry, _response, host, registry, lifecycle, scheduled = guarded_shutdown
    widget._table.item(0, COL_TRANSLATION).setText("Unsaved draft")
    assert entry.translation == "Old" and not context.dirty
    answer = QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel
    question = Mock(return_value=answer)
    monkeypatch.setattr(QMessageBox, "question", question)

    event = QCloseEvent()
    assert not lifecycle.close_event(event)
    assert not event.isAccepted()
    question.assert_called_once()
    assert question.call_args.args[-1] == QMessageBox.StandardButton.Cancel
    assert not scheduled
    if discard:
        assert widget._inline_edits.view.isHidden()
        host.save_current_project_async.call_args.kwargs["on_finished"](True)
        host.close.assert_called_once()
    else:
        assert widget._inline_edits.view.text.toPlainText() == "Unsaved draft"
        host.save_current_project_async.assert_not_called()
        _assert_exit_cancelled(host, registry, lifecycle)


@pytest.mark.parametrize("discard", [False, True])
def test_final_save_checks_drafts_created_after_close_started(guarded_shutdown, monkeypatch, discard):
    widget, context, entry, _response, host, registry, lifecycle, scheduled = guarded_shutdown
    answer = QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel
    question = Mock(return_value=answer)
    monkeypatch.setattr(QMessageBox, "question", question)
    lifecycle.close_event(QCloseEvent())
    assert registry.shutting_down and host.close_pending
    question.assert_not_called()

    widget._table.item(0, COL_TRANSLATION).setText("Late draft")
    assert entry.translation == "Old" and not context.dirty
    callback = host.save_current_project_async.call_args.kwargs["on_finished"]
    callback(True)

    question.assert_called_once()
    assert not scheduled
    if discard:
        assert widget._inline_edits.view.isHidden()
        host.close.assert_called_once()
    else:
        assert widget._inline_edits.view.text.toPlainText() == "Late draft"
        _assert_exit_cancelled(host, registry, lifecycle)
        # A late completion notification must not restart a cancelled close.
        callback(True)
        lifecycle.begin_background_close()
        host.close.assert_not_called()
        assert host.save_current_project_async.call_count == 1


@pytest.mark.parametrize("resolve", ["retry", "discard"])
def test_resolved_draft_allows_normal_close_without_another_prompt(guarded_shutdown, monkeypatch, resolve):
    widget, _context, entry, response, host, _registry, lifecycle, _scheduled = guarded_shutdown
    widget._table.item(0, COL_TRANSLATION).setText("Draft")
    response.is_success = True
    getattr(widget._inline_edits, resolve)()
    assert entry.translation == ("Draft" if resolve == "retry" else "Old")
    question = Mock()
    monkeypatch.setattr(QMessageBox, "question", question)

    lifecycle.close_event(QCloseEvent())
    host.save_current_project_async.call_args.kwargs["on_finished"](True)

    question.assert_not_called()
    host.close.assert_called_once()


def test_discard_confirmation_does_not_cover_a_newer_draft(guarded_shutdown, monkeypatch):
    widget, _context, _entry, _response, host, registry, lifecycle, _scheduled = guarded_shutdown
    widget._table.item(0, COL_TRANSLATION).setText("Draft before confirmation")

    def answer(*_args):
        widget._table.item(0, COL_TRANSLATION).setText("New draft while confirming")
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "question", answer)
    lifecycle.close_event(QCloseEvent())

    assert widget._inline_edits.view.text.toPlainText() == "New draft while confirming"
    host.save_current_project_async.assert_not_called()
    _assert_exit_cancelled(host, registry, lifecycle)


@pytest.mark.parametrize("discard", [False, True])
def test_final_draft_confirmation_rechecks_late_project_edit(guarded_shutdown, monkeypatch, discard):
    widget, _context, _entry, _response, host, registry, lifecycle, scheduled = guarded_shutdown
    lifecycle.close_event(QCloseEvent())
    widget._table.item(0, COL_TRANSLATION).setText("Late draft")

    def answer(*_args):
        host.context.dirty = True
        return QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel

    question = Mock(side_effect=answer)
    monkeypatch.setattr(QMessageBox, "question", question)
    callback = host.save_current_project_async.call_args.kwargs["on_finished"]
    callback(True)

    question.assert_called_once()
    _assert_exit_cancelled(host, registry, lifecycle)
    assert not scheduled
    assert widget._inline_edits.view.isHidden() is discard
    callback(True)
    host.close.assert_not_called()


@pytest.mark.parametrize("kind", ["api", "project_open", "foreground", "ai"])
def test_final_draft_confirmation_drains_new_background_work_before_disposal(guarded_shutdown, monkeypatch, kind):
    widget, _context, _entry, _response, host, registry, lifecycle, scheduled = guarded_shutdown
    api_registry = SimpleNamespace(busy=False)
    monkeypatch.setattr(window_lifecycle, "get_api_worker_registry", lambda: api_registry)
    worker = Mock()
    worker.isRunning.return_value = False
    lifecycle.close_event(QCloseEvent())
    widget._table.item(0, COL_TRANSLATION).setText("Late draft")

    def answer(*_args):
        if kind == "api":
            api_registry.busy = True
        elif kind == "ai":
            registry.busy = True
        else:
            worker.isRunning.return_value = True
            setattr(host, f"{kind}_worker", worker)
        return QMessageBox.StandardButton.Discard

    question = Mock(side_effect=answer)
    monkeypatch.setattr(QMessageBox, "question", question)
    host.save_current_project_async.call_args.kwargs["on_finished"](True)

    question.assert_called_once()
    assert host.close_pending and not host.close_ready
    assert len(scheduled) == 1 and scheduled[0][0] > 0
    registry.dispose.assert_not_called()
    host.context.close_projection.assert_not_called()
    host.close.assert_not_called()
    assert widget._inline_edits.view.isHidden()

    api_registry.busy = registry.busy = False
    worker.isRunning.return_value = False
    scheduled.pop()[1]()

    question.assert_called_once()
    assert not scheduled
    assert host.save_current_project_async.call_count == 1
    registry.dispose.assert_called_once()
    host.context.close_projection.assert_called_once()
    host.close.assert_called_once()
