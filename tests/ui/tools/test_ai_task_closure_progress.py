"""Project closure reports bounded work and remains interruptible on its worker."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QProgressBar
import pytest

from tests.ui.tools import test_unified_task_progress as qt_support
from tests.ui.tools.test_ai_task_term_closure import _close, _source
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor
from transbridge.ui.tools.ai_translator.task_progress_presentation import TaskProgressPresentation
from transbridge.ui.tools.ai_translator.task_term_closure import close_task_terms
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker

qapp = qt_support.qapp


@pytest.mark.parametrize("resume", [True, False])
def test_reference_scan_pauses_at_entry_boundary_and_can_resume_or_cancel(resume):
    outcome = _source(rows=[(str(i), "Riverwood", "溪木镇", "succeeded") for i in range(3)])
    pause, stop, paused = Event(), Event(), Event()
    pause.set()
    visited, progress = [], []

    def resolve(_task, entry):
        visited.append(entry.id)
        if len(visited) == 1:
            pause.clear()
            paused.set()
        return {"Riverwood": "溪木镇"}

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _close,
            (outcome,),
            resolve_terms=resolve,
            stop_event=stop,
            pause_event=pause,
            progress=lambda *args: progress.append(args),
            repair=lambda *_: pytest.fail("unexpected review"),
        )
        try:
            assert paused.wait(3)
            time.sleep(0.07)
            assert visited == ["0"] and not future.done()
            pause.set() if resume else stop.set()
            (closed,) = future.result(timeout=3)
        finally:
            stop.set()
            pause.set()
    statuses = [result.status for result in closed.translation.entry_outcomes.values()]
    assert statuses == (["succeeded"] * 3 if resume else ["succeeded", "cancelled", "cancelled"])
    assert all(entry.translation == "溪木镇" for entry in closed.task.collection)
    assert progress[-1][1] == ("term_closure_done" if resume else "term_closure_cancelled")
    assert progress[-1][2:4] == ((3, 3) if resume else (1, 3))


def test_reference_scan_reports_before_lookup_and_completes_no_difference_sources():
    outcome = _source()
    progress = []

    def resolve(*_):
        assert progress[0][1] == "term_closure_load"
        assert progress[-1][1:4] == ("term_closure_scan", 0, 1)
        return {}

    (closed,) = _close((outcome,), resolve_terms=resolve, progress=lambda *args: progress.append(args))
    assert closed.task is outcome.task and closed.translation is outcome.translation
    assert progress[-1][1:4] == ("term_closure_done", 1, 1)
    assert "无需补充校对" in progress[-1][4]


@pytest.mark.slow
def test_twenty_thousand_entry_closure_updates_qt_with_bounded_feedback(qapp):
    outcomes = {
        key: _source(key, [(str(i), "Riverwood", "溪木镇", "succeeded") for i in range(10_000)]) for key in ("A", "B")
    }
    tasks = tuple(outcome.task for outcome in outcomes.values())
    emitted, observed, completed, ticks, states = [], [], [], [], []
    view = TaskProgressPresentation(tasks)
    label, bar = QLabel(), QProgressBar()

    class Executor(SourceExecutor):
        def prepare_names(self, _tasks):
            pass

        def execute(self, task):
            return outcomes[task.key]

        def close_terms(self, values):
            def progress(*args):
                emitted.append(args)
                self.progress(*args)

            return close_task_terms(
                values,
                resolve_terms=lambda *_: {"Riverwood": "溪木镇"},
                repair=lambda *_: pytest.fail("no model calls expected"),
                stop_event=self.stop,
                pause_event=self.pause,
                progress=progress,
            )

    request = SimpleNamespace(config=LLMConfig(retrieval_enabled=False), request_budget=AiRequestBudget(2))
    worker = AiTaskWorker(request, tasks, executor_factory=Executor)
    worker.enable_feedback_polling()
    worker.source_started.connect(view.source_started)
    worker.source_finished.connect(view.source_finished)
    worker.completed.connect(completed.append)

    def render(*args):
        observed.append(args)
        view.source_progress(*args)
        label.setText(view.summary)
        current, total, text = view.progress
        bar.setRange(0, total)
        bar.setValue(current)
        bar.setFormat(text)

    worker.progress.connect(render)
    timer = QTimer()

    def poll():
        ticks.append(time.monotonic())
        states.append(len(worker._feedback._states))
        worker.poll_feedback()

    timer.timeout.connect(poll)
    timer.start(5)
    started = time.monotonic()
    worker.start()
    try:
        deadline = started + 15
        while (not completed or worker.isRunning()) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.001)
        assert completed and not worker.isRunning()
        assert len(ticks) >= 3
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.5
        assert max(states) <= len(tasks)
        assert len(emitted) < 250  # 20,000 entries never become 20,000 queued UI events.
        assert any(row[1] == "term_closure_scan" and 0 < row[2] < row[3] for row in observed)
        assert label.text().endswith("正在整理任务结果")
        assert all(outcome.translation.success_count == 10_000 for outcome in completed[0])
        assert request.request_budget.snapshot().in_flight == 0
        print({
            "entries": 20_000,
            "seconds": round(time.monotonic() - started, 3),
            "max_qt_gap_ms": round(max(b - a for a, b in zip(ticks, ticks[1:])) * 1000, 1),
            "emitted": len(emitted),
            "rendered": len(observed),
            "max_buffered_states": max(states),
        })
    finally:
        timer.stop()
        worker.stop()
        worker.wait(5000)
        label.close()
        bar.close()
