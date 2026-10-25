"""Slow result phases must not own the Qt thread or publish after cancellation."""

import threading

from PyQt6.QtCore import QTimer
import pytest

from tests.ui.tools import test_unified_task_progress as fixtures
from tests.ui.tools.test_unified_task_progress import (
    _start,
    _wait_reports,
    _wait_until,
)
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.tools.ai_translator import task_result_flow
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome

make_window = fixtures.make_window
qapp = fixtures.qapp


@pytest.mark.parametrize("phase", ["prepare_outcomes", "prepare_application"])
def test_cancel_and_close_remain_responsive_during_result_preparation(qapp, make_window, monkeypatch, phase):
    window, session, _ = make_window()
    entered, release = threading.Event(), threading.Event()
    operation = getattr(task_result_flow, phase)
    threads, pulses = [], []

    def blocked(*args):
        threads.append(threading.get_ident())
        entered.set()
        if not release.wait(5):
            raise TimeoutError("result preparation test did not release worker")
        return operation(*args)

    monkeypatch.setattr(task_result_flow, phase, blocked)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: pulses.append(True))
    timer.start()
    try:
        worker = _start(window, session)
        worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
        _wait_until(lambda: entered.is_set() and len(pulses) >= 3)
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        assert window.run.result_preparation.busy and session.commits == 0
        window.show()
        window.close()
        assert not window.isVisible()
        window.run.cancel()
        assert window.run.cancelled and session.commits == 0
    finally:
        release.set()
        timer.stop()
        _wait_reports(qapp, window)
    assert window.run.state == "cancelled" and session.commits == 0
    record = TaskHistoryStore(session.history_dir).load(window.request.run_id)
    assert record["state"] == "cancelled" and not record["applied"]


def test_version_change_after_commit_keeps_applied_evidence_and_reports_error(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    entered, release = threading.Event(), threading.Event()
    operation = task_result_flow.prepare_applied

    def blocked(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("applied projection test did not release worker")
        return operation(*args)

    monkeypatch.setattr(task_result_flow, "prepare_applied", blocked)
    try:
        worker = _start(window, session)
        worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
        _wait_until(entered.is_set)
        assert session.commits == 1

        def switched():
            raise RuntimeError("活动版本已变化")

        monkeypatch.setattr(session, "finish", switched)
    finally:
        release.set()
        _wait_reports(qapp, window)
    assert window.run.state == "failed" and "活动版本已变化" in window.status.text()
    assert len(window.run.entries.applied_keys) == 2
    assert TaskHistoryStore(session.history_dir).load(window.request.run_id)["applied"]
