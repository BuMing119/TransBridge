"""Explicit comparison and cancellation protect the reviewed execution intent."""

from types import SimpleNamespace

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.operations import test_paratranz_sync_dialog as sync_support
from transbridge.ui.operations.plan_presenter import OperationPlanError

qapp = sync_support.qapp
sync_case = sync_support.sync_case


def test_cancel_during_comparison_closes_session_and_ignores_worker_result(sync_case, qapp):
    case = sync_case
    case.dialog = dialog = case.facade.begin_download(SimpleNamespace())
    dialog._confirm.click()
    assert dialog._preflight_running
    assert dialog._cancel.isEnabled()
    assert not dialog._choose_project.isEnabled()
    assert not dialog._strategy_buttons["prefer_local"].isEnabled()

    dialog._cancel.click()
    case.dialog = None
    qapp.processEvents()
    case.workers[0].complete()

    assert not case.submitted
    assert case.facade.active_plan_count == 0
    assert case.workers[0].deleted


def test_editing_mode_rejects_previous_confirmation_until_new_comparison(sync_case, qapp):
    case = sync_case
    case.dialog = dialog = case.facade.begin_download(SimpleNamespace())
    dialog._confirm.click()
    case.workers[0].complete()
    token = dialog._preflight.confirmation_token
    assert token is not None

    dialog._strategy_buttons["prefer_local"].click()
    QApplication.processEvents()
    assert len(case.workers) == 1
    assert dialog._confirm.text() == "开始对照"
    assert dialog._preflight is None

    # A queued activation carrying the earlier token cannot execute the old plan.
    dialog.confirm_requested.emit(dialog._plan.session_id, token)
    assert not case.submitted
    assert dialog._preflight_running
    case.workers[-1].complete()
    assert dialog._confirm.isEnabled()
    assert dialog._preflight.confirmation_token != token


def test_already_computed_result_arriving_after_close_cannot_restore_confirmation(sync_case, qapp):
    case = sync_case
    case.dialog = dialog = case.facade.begin_download(SimpleNamespace())
    dialog._confirm.click()
    worker = case.workers[0]
    checked = worker.fn()  # Result computed; its Qt delivery is still pending.
    session_id = dialog._plan.session_id
    assert checked.confirmation_token is not None
    dialog._cancel.click()
    case.dialog = None
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    worker.result.emit(checked)
    worker.finished.emit()
    assert not case.submitted
    assert case.facade.active_plan_count == 0
    assert worker.deleted
    with pytest.raises(OperationPlanError, match="closed or unknown"):
        case.facade._coordinator._presenter.confirm(session_id, checked.confirmation_token, owner_id="gui")
