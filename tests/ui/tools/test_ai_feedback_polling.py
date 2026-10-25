"""GUI stalls must not turn worker updates into an unbounded Qt event backlog."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from PyQt6.QtCore import pyqtSignal
import pytest

from tests.ui.tools import test_unified_task_progress as support
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.translation.ai_request_budget import AiRequestBudgetSnapshot
from transbridge.ui.tools.ai_translator import task_run
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_feedback import TaskFeedback
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker

make_window = support.make_window
qapp = support.qapp


class _PollingWorker(support._Worker):
    log_batch = pyqtSignal(object)
    source_finished = pyqtSignal(str)
    project_name_progress = pyqtSignal(object)
    request_state_changed = pyqtSignal(object)
    enable_feedback_polling = AiTaskWorker.enable_feedback_polling
    poll_feedback = AiTaskWorker.poll_feedback

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._feedback = TaskFeedback(
            progress=self.progress.emit,
            project_progress=self.project_name_progress.emit,
            log_batch=self.log_batch.emit,
            request_state=self.request_state_changed.emit,
        )


def _produce(worker, count=20_000):
    feedback = worker._feedback
    for task in worker.tasks:
        feedback.barrier(worker.source_started.emit, task.key)
    for index in range(1, count + 1):
        for task in worker.tasks:
            feedback.progress(task.key, "translate", index, count, "正在翻译")
        feedback.log("first", f"正在翻译 {index}")
        feedback.request_state(AiRequestBudgetSnapshot(index % 4, 0, 4))
        # Simulate thousands of producer-side monitor ticks while GUI cannot run.
        feedback.drain()


def test_stalled_gui_receives_latest_progress_and_request_state_without_replaying_old_events(
    make_window, monkeypatch, qapp
):
    monkeypatch.setattr(task_run, "AiTaskWorker", _PollingWorker)
    window, session, _ = make_window()
    worker = support._start(window, session)
    seen, logs, requests = [], [], []
    worker.progress.connect(lambda *args: seen.append((args, threading.get_ident())))
    window.run.logged.connect(logs.append)
    worker.request_state_changed.connect(requests.append)
    thread = threading.Thread(target=_produce, args=(worker,))
    thread.start()
    thread.join(timeout=10)  # Deliberately do not process Qt events.
    assert not thread.is_alive()
    assert not seen and not requests and not logs

    support._wait_until(lambda: len(seen) == 2)
    assert all(args[2] == 20_000 and owner == threading.get_ident() for args, owner in seen)
    assert len(requests) == 1 and requests[0].in_flight == 0
    assert window.run.progress_view.source_rows["first"].current == 20_000
    assert "省略 17000 条" in "\n".join(logs)
    assert "正在翻译 20000" in window.logs.toPlainText()
    qapp.processEvents()
    assert len(seen) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_finished_drains_last_values_once_and_preserves_save_and_cancel(make_window, monkeypatch, cancel):
    monkeypatch.setattr(task_run, "AiTaskWorker", _PollingWorker)
    window, session, _ = make_window()
    worker = support._start(window, session)
    # Force the finished signal, rather than the timer, to deliver the tail.
    window.run.worker_binding._timer.stop()
    window.pause_button.click()
    assert worker.is_paused
    window.pause_button.click()
    assert not worker.is_paused
    if cancel:
        window.stop_button.click()
    seen = []
    worker.progress.connect(lambda *args: seen.append("progress"))
    worker.source_finished.connect(lambda key: seen.append("source-finished"))
    worker.completed.connect(lambda outcomes: seen.append("completed"))
    outcomes = []
    for task in session.tasks:
        for entry in task.collection:
            entry.translation = "保存的合成译文"
        outcomes.append(
            SourceOutcome(
                task,
                translation=SimpleNamespace(
                    entry_outcomes={
                        entry.identity: TranslationEntryOutcome(
                            entry.identity, "succeeded", entry.translation, entry.stage
                        )
                        for entry in task.entries
                    }
                ),
            )
        )

    def finish():
        _produce(worker, 1000)
        for task in worker.tasks:
            worker._feedback.barrier(worker.source_finished.emit, task.key)
        worker._feedback.finish(worker.completed.emit, tuple(outcomes))
        worker.running = False
        worker.finished.emit()

    thread = threading.Thread(target=finish)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and seen == []
    support._wait_until(lambda: window.run.worker is None)
    support._wait_reports(None, window)
    assert seen == ["progress", "progress", "source-finished", "source-finished", "completed"]
    assert session.commits == (0 if cancel else 1)
    assert session.saved is (not cancel)
    worker._feedback.progress("first", "translate", 0, 1000, "迟到状态")
    worker.poll_feedback(all_pending=True)
    assert seen.count("completed") == 1


def test_gui_delivery_releases_buffer_lock_before_callbacks():
    entered, published = threading.Event(), threading.Event()

    def progress(*args):
        entered.set()
        assert published.wait(2)

    feedback = TaskFeedback(progress=progress, project_progress=lambda _: None, log_batch=lambda _: None)
    feedback.enable_polling()
    feedback.progress("one", "translate", 1, 2, "正在翻译")

    def publish():
        assert entered.wait(2)
        feedback.progress("one", "translate", 2, 2, "处理完成")
        published.set()

    thread = threading.Thread(target=publish)
    thread.start()
    try:
        feedback.poll()
    finally:
        thread.join(timeout=3)
    assert not thread.is_alive()
