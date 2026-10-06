from __future__ import annotations

from datetime import UTC, datetime
import sys
import threading
import time
from types import SimpleNamespace
import uuid

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QThreadPool
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import JobSpec, OwnerRef, TaskRuntime
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncViewState
from transbridge.ui.tools.terminology.sync_view import TerminologySyncPanel
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _drain_until(predicate) -> None:
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_runtime_activity_reaches_controls_only_on_gui_thread_and_detaches_on_close(monkeypatch) -> None:
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: str(uuid.uuid4())),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    owner = OwnerRef("operator", "gui", "project", "variant")
    rendered_on = []
    original = TerminologySyncPanel.render_activity

    def render(panel, activity):
        rendered_on.append(threading.get_ident())
        original(panel, activity)

    monkeypatch.setattr(TerminologySyncPanel, "render_activity", render)
    presenter = TerminologyPresenter(
        TerminologyUiServices(sync=object(), runtime=runtime),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    job = JobSpec("operation.terminology_sync.backup", "input", "hash")

    def publish():
        runtime.submit(job, owner)

    worker = threading.Thread(target=publish)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    assert not rendered_on
    _drain_until(lambda: bool(rendered_on))
    assert rendered_on == [threading.get_ident()]
    assert not window.sync_view.backup_button.isEnabled()

    window.close()
    assert window._sync_tasks.closed
    runtime.submit(job, owner)
    _APP.processEvents()
    assert rendered_on == [threading.get_ident()]
    assert window.sync_view._signals.closed
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("fails", [False, True], ids=["success", "error"])
def test_background_result_after_parent_deletion_never_emits_to_deleted_qobject(monkeypatch, fails) -> None:
    exceptions = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: exceptions.append(error))
    parent = QWidget()
    panel = TerminologySyncPanel(SimpleNamespace(state=TerminologySyncViewState()), parent)
    pool = QThreadPool()
    panel._pool = pool
    signals = panel._signals
    results = []
    signals.completed.connect(results.append)
    signals.failed.connect(results.append)
    started = threading.Event()
    release = threading.Event()

    def delayed():
        started.set()
        assert release.wait(3)
        if fails:
            raise ValueError("delayed read failed")
        return TerminologySyncViewState()

    panel._run("plan", delayed)
    try:
        assert started.wait(3)
        # Exercise destruction even when a parent is removed without calling dispose.
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert sip.isdeleted(panel)
        assert signals.closed
        assert not sip.isdeleted(signals)
    finally:
        release.set()
        assert pool.waitForDone(3000)
    _APP.processEvents()
    assert not exceptions
    assert not results


@pytest.mark.parametrize("fails", [False, True], ids=["success", "error"])
def test_open_panel_receives_background_success_and_failure_on_gui_thread(fails) -> None:
    rendered_on = []

    class RecordingPanel(TerminologySyncPanel):
        def render_sync(self, state):
            rendered_on.append(threading.get_ident())
            super().render_sync(state)

    panel = RecordingPanel(SimpleNamespace(state=TerminologySyncViewState()))
    pool = QThreadPool()
    panel._pool = pool
    executed_on = []

    def call():
        executed_on.append(threading.get_ident())
        if fails:
            raise ValueError("read failed")
        return TerminologySyncViewState(error="result received")

    panel._run("plan", call)
    assert pool.waitForDone(3000)
    assert not rendered_on
    _drain_until(lambda: bool(rendered_on))
    assert executed_on[0] != threading.get_ident()
    assert rendered_on == [threading.get_ident()]
    assert panel.summary.text() == ("read failed" if fails else "result received")
    assert panel.backup_button.isEnabled()
    panel.dispose()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_dispose_discards_already_queued_results_and_does_not_start_more_work() -> None:
    panel = TerminologySyncPanel(SimpleNamespace(state=TerminologySyncViewState()))
    text = panel.summary.text()
    panel._signals.completed.emit(TerminologySyncViewState(error="late result"))
    panel._signals.failed.emit(ValueError("late error"))
    panel.dispose()
    panel.dispose()
    calls = []
    panel._run("plan", lambda: calls.append("unexpected"))
    _APP.processEvents()
    assert panel.summary.text() == text
    assert not calls
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_window_destruction_without_close_detaches_sync_runtime_subscription() -> None:
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: str(uuid.uuid4())),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    presenter = TerminologyPresenter(
        TerminologyUiServices(sync=object(), runtime=runtime),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    adapter = window._sync_tasks
    signals = window.sync_view._signals
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert adapter.closed
    assert signals.closed
    presenter.close()
