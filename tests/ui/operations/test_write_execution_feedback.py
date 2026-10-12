from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from threading import Thread
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations import production
from transbridge.ui.operations.write_dialog import WritePlanDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _finish_preflight(facade, dialog):
    deadline = monotonic() + 5
    while facade._coordinator._preflight_workers and monotonic() < deadline:
        QTest.qWait(10)
    assert not facade._coordinator._preflight_workers
    assert dialog._preflight is not None, dialog._checks.text()


@pytest.fixture
def write_case(tmp_path, qapp, monkeypatch):
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    parsed = TranslationIoUseCase().parse(
        ParseRequest(
            SourceDescriptor(str(source), source.name, source.stat().st_size), RequestContext("gui"), FormatId.XML_EET
        )
    )
    context = SimpleNamespace(
        active_slot=SimpleNamespace(
            source_snapshot=parsed.source_snapshot,
            format_id=FormatId.XML_EET,
            collection=TranslationEntryCollection((replace(parsed.entries[0], translation="新译文", stage=1),)),
            esp_path=str(source),
        )
    )
    queued = []
    tasks = TaskRuntime(
        id_generator=_Ids(),
        clock=_Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    subscriptions = []
    subscribe = tasks.subscribe

    def track_subscription(*args, **kwargs):
        subscription = subscribe(*args, **kwargs)
        subscriptions.append(subscription)
        return subscription

    monkeypatch.setattr(tasks, "subscribe", track_subscription)
    facade = production.build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))
    target = tmp_path / "translated.xml"
    dialog = facade.begin_write(context, target_path=str(target))
    assert isinstance(dialog, WritePlanDialog)
    dialog._preflight_button.click()
    _finish_preflight(facade, dialog)
    assert dialog._confirm_button.isEnabled()
    case = SimpleNamespace(
        tasks=tasks,
        facade=facade,
        dialog=dialog,
        target=target,
        queued=queued,
        subscriptions=subscriptions,
        owner=OwnerRef("gui", "gui.operation-plan"),
    )
    yield case
    if not sip.isdeleted(dialog):
        dialog.set_execution_running(False)
        dialog.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("outcome", ["completed", "failed", "partial", "cancelled"])
@pytest.mark.parametrize("early", [False, True])
def test_write_reports_terminal_result_with_real_runtime(write_case, qapp, monkeypatch, outcome, early):
    case = write_case
    subject = case.dialog
    message = "回读内容与预期不符 <record>"
    results = {
        "completed": OperationResult.completed(artifact_refs=(str(case.target),)),
        "failed": OperationResult.failed(DomainError(ErrorCategory.EXTERNAL, "FIDELITY_MISMATCH", message)),
        "partial": OperationResult.partial(
            {},
            counts=OperationCounts(succeeded=1, failed=1),
            diagnostics=(Diagnostic("WRITE_BATCH_INCOMPLETE", message),),
            artifact_refs=(str(case.target),),
        ),
        "cancelled": OperationResult.cancelled(),
    }
    monkeypatch.setattr(production.HydratedWriteWorkload, "__call__", lambda _self, _context: results[outcome])
    if early:
        # Complete synchronously inside schedule, before the observer subscribes.
        monkeypatch.setattr(CallbackThreadBackend, "start", lambda _self, _run_id, target: target())
    token = subject._preflight.confirmation_token
    subject._confirm_button.click()
    subject.confirm_requested.emit(subject._plan.session_id, token)
    subject._confirm_button.click()
    assert len(case.tasks.list(case.owner)) == 1
    assert subject.isVisible()
    assert subject._execution_running
    assert subject._progress.isVisible()
    assert not subject._preflight_button.isEnabled()
    assert not subject._edit_button.isEnabled()
    assert not subject._close_button.isEnabled()
    QTest.keyClick(subject, Qt.Key.Key_Escape)
    assert not subject.close()
    assert subject.isVisible()
    if not early:
        worker = Thread(target=case.queued.pop())
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        # Background callbacks only queue Qt signals; they cannot alter widgets.
        assert subject._execution_running
    qapp.processEvents()

    assert subject.isVisible()
    assert not subject._execution_running
    assert subject._checks.property("tbStatusId") == outcome
    assert subject._close_button.text() == "关闭"
    assert subject._close_button.isEnabled()
    assert subject._preflight_button.isEnabled()
    assert not subject._confirm_button.isEnabled()
    assert all(subscription.closed for subscription in case.subscriptions)
    if outcome == "completed":
        assert "写回完成" in subject._checks.text()
        assert "预检" not in subject._checks.text()
    else:
        assert "如需重试，请重新运行预检" in subject._checks.text()
    if outcome in {"completed", "partial"}:
        assert str(case.target) in subject._checks.text()
    if outcome in {"failed", "partial"}:
        assert message in subject._checks.text()
        assert results[outcome].diagnostics[0].code not in subject._checks.text()
        assert results[outcome].diagnostics[0].code in subject._details.toPlainText()
        assert not subject._details.isVisible()
        subject._details_button.click()
        assert subject._details.isVisible()
    assert case.facade.active_plan_count == 1
    assert len(case.facade._coordinator._presenter._sessions) == 1
    # Closing, even via Accepted, releases the session immediately.
    subject.accept()
    assert case.facade.active_plan_count == 0
    assert not case.facade._coordinator._presenter._sessions


def test_real_write_displays_the_committed_output_and_can_run_fresh_preflight(write_case, qapp):
    case = write_case
    subject = case.dialog
    subject._confirm_button.click()
    worker = Thread(target=case.queued.pop())
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    qapp.processEvents()

    assert case.tasks.list(case.owner)[0].state is JobState.COMPLETED
    assert case.target.exists()
    assert str(case.target) in subject._checks.text()
    assert "写回完成" in subject._checks.text()
    subject._preflight_button.click()
    _finish_preflight(case.facade, subject)
    assert not subject._confirm_button.isEnabled()  # The output now exists, requiring explicit overwrite.
    subject._edit_button.click()
    subject._field_edits["overwrite_confirmed"].setChecked(True)
    subject._preflight_button.click()
    _finish_preflight(case.facade, subject)
    assert subject._confirm_button.isEnabled()


def test_destroyed_running_write_releases_projection_and_session(write_case, qapp):
    case = write_case
    subject = case.dialog
    subject._confirm_button.click()
    subject.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(subscription.closed for subscription in case.subscriptions)
    assert case.facade.active_plan_count == 0
    assert not case.facade._coordinator._presenter._sessions
    worker = Thread(target=case.queued.pop())
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    qapp.processEvents()
    assert case.tasks.list(case.owner)[0].state is JobState.COMPLETED


def test_real_publication_durability_failure_is_partial_in_dialog_and_task_center(write_case, qapp, monkeypatch):
    from transbridge.application.io.publish.filesystem import OsPublishFilesystem
    from transbridge.application.tasks.activity import activity_from_snapshot
    from transbridge.ui.shell.task_center import TaskCenterPanel

    def fail_sync(_self, _path):
        raise OSError("injected durability failure")

    monkeypatch.setattr(OsPublishFilesystem, "fsync_directory", fail_sync)
    case = write_case
    case.dialog._confirm_button.click()
    case.queued.pop()()
    qapp.processEvents()
    assert case.target.exists()
    assert case.dialog._checks.property("tbStatusId") == "partial"
    assert "未能确认已安全保存" in case.dialog._checks.text()
    assert "已完成" not in case.dialog._checks.text()
    panel = TaskCenterPanel()
    try:
        panel.render_activity(activity_from_snapshot(case.tasks.list(case.owner)[0]))
        assert "部分完成" in panel._current.item(0).text()
    finally:
        panel.close()
