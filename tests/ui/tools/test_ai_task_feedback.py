"""Bounded feedback and ordered lifecycle delivery before Qt's event queue."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.project_name_progress import ProjectNameProgress, ProjectNameSourceProgress
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_feedback import TaskFeedback
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


def _feedback(**kwargs):
    events = []
    feedback = TaskFeedback(
        progress=lambda *args: events.append(("progress", args)),
        project_progress=lambda event: events.append(("project", event)),
        log_batch=lambda rows: events.append(("logs", rows)),
        **kwargs,
    )
    return feedback, events


def _project(phase, current=0):
    return ProjectNameProgress(phase, current, 10, phase, (ProjectNameSourceProgress("A", current, 10, phase),))


def _task(key):
    entry = TranslationEntry("id", "key", "Name", "", 0, "WEAP:FULL")
    return SourceTask(key, key, None, TranslationEntryCollection((entry,)), (entry,), ())


def test_latest_progress_per_source_stage_and_project_phase_keep_update_order():
    feedback, events = _feedback()
    feedback.progress("A", "terms", 1, 10, "加载中")
    feedback.project_progress(_project("scan", 1))
    feedback.log("A", "开始翻译")
    feedback.progress("A", "terms", 10, 10, "已加载")
    feedback.progress("A", "translate", 2, 10, "第一轮")
    feedback.progress("B", "terms", 3, 5, "加载中")
    feedback.project_progress(_project("scan", 10))
    feedback.project_progress(_project("load"))
    assert events == []
    feedback.drain()
    assert events == [
        ("logs", (("A", "开始翻译"),)),
        ("progress", ("A", "terms", 10, 10, "已加载")),
        ("progress", ("A", "translate", 2, 10, "第一轮")),
        ("progress", ("B", "terms", 3, 5, "加载中")),
        ("project", _project("scan", 10)),
        ("project", _project("load")),
    ]


def test_log_burst_is_filtered_bounded_and_reports_omitted_messages():
    feedback, events = _feedback()
    for index in range(10_000):
        feedback.log("A", f"Name {index} -> 译文")
        feedback.log("A", f"开始翻译 {index}")
    feedback.log("A", "模型请求失败：连接中断")
    assert events == []
    feedback.drain()
    assert sum(len(rows) for kind, rows in events if kind == "logs") == 100
    feedback.finish(lambda: events.append(("completed", None)))
    batches = [rows for kind, rows in events if kind == "logs"]
    assert all(len(rows) <= 100 for rows in batches)
    lines = [text for rows in batches for _, text in rows]
    assert len(lines) == 3001  # The bounded tail plus one explicit omission notice.
    assert "省略 7001 条" in lines[0]
    assert lines[1] == "开始翻译 7001"
    assert lines[-1] == "模型请求失败：连接中断"
    assert all("Name" not in line for line in lines)
    assert events[-1] == ("completed", None)


def test_log_backlog_does_not_delay_latest_progress():
    feedback, events = _feedback(batch_size=2)
    for index in range(3):
        feedback.log("A", f"开始翻译 {index}")
    feedback.progress("A", "translate", 3, 3, "处理完成")
    feedback.drain()
    assert events == [
        ("logs", (("A", "开始翻译 0"), ("A", "开始翻译 1"))),
        ("progress", ("A", "translate", 3, 3, "处理完成")),
    ]
    feedback.drain()
    assert events[-1] == ("logs", (("A", "开始翻译 2"),))


def test_continuous_log_overload_delivers_latest_source_and_project_state_each_drain():
    feedback, events = _feedback(max_logs=8, batch_size=2)
    published, consumed = Queue(), Queue()

    def produce():
        for current in range(1, 11):
            for index in range(20):
                feedback.log("A", f"翻译进度 {current}-{index}")
            feedback.progress("A", "translate", current, 10, "正在翻译")
            feedback.project_progress(_project("validate", current))
            published.put(current)
            assert consumed.get(timeout=3) == current

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(produce)
        for current in range(1, 11):
            assert published.get(timeout=3) == current
            before = len(events)
            feedback.drain()
            delivered = events[before:]
            assert sum(len(rows) for kind, rows in delivered if kind == "logs") == 2
            assert [args[2] for kind, args in delivered if kind == "progress"] == [current]
            assert [event.current for kind, event in delivered if kind == "project"] == [current]
            consumed.put(current)
        producer.result(timeout=3)
    feedback.finish(lambda: events.append(("completed", None)))
    assert [text for kind, rows in events if kind == "logs" for _, text in rows][-1] == "翻译进度 10-19"
    assert events[-1] == ("completed", None)


def test_state_buffer_is_bounded_even_for_unexpected_many_stages():
    feedback, events = _feedback(max_states=3)
    for index in range(20):
        feedback.progress("A", f"stage-{index}", index, 20, "正在处理")
    feedback.drain()
    assert "省略 17 条" in events[0][1][0][1]
    assert [args[1] for kind, args in events if kind == "progress"] == ["stage-17", "stage-18", "stage-19"]


def test_concurrent_producers_keep_every_sources_latest_count_and_final_log():
    feedback, events = _feedback(max_logs=20)

    def produce(source):
        for current in range(1, 501):
            feedback.progress(source, "translate", current, 500, "正在翻译")
            feedback.log(source, f"翻译进度 {current}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(produce, source) for source in ("A", "B", "C", "D")]
        for future in futures:
            future.result(timeout=3)
    feedback.log("D", "处理结束")
    feedback.finish(lambda: events.append(("completed", None)))
    assert {args[0]: args[2] for kind, args in events if kind == "progress"} == {
        "A": 500,
        "B": 500,
        "C": 500,
        "D": 500,
    }
    lines = [text for kind, rows in events if kind == "logs" for _, text in rows]
    assert len(lines) == 21
    assert lines[-1] == "处理结束"
    feedback.progress("A", "translate", 0, 500, "过期回调")
    feedback.log("A", "开始过期日志")
    feedback.project_progress(_project("scan"))
    feedback.drain()
    assert events[-1] == ("completed", None)


def test_source_barrier_cannot_overtake_monitor_delivery():
    entered, release, requested, finished = (Event() for _ in range(4))
    events = []

    def logs(rows):
        entered.set()
        assert release.wait(3)
        events.append(("logs", rows))

    feedback = TaskFeedback(
        progress=lambda *args: events.append(("progress", args)),
        project_progress=lambda _: None,
        log_batch=logs,
    )
    feedback.log("A", "开始翻译")
    feedback.progress("A", "translate", 1, 1, "处理完成")

    def barrier():
        requested.set()
        feedback.barrier(lambda: (events.append(("finished", "A")), finished.set()))

    with ThreadPoolExecutor(max_workers=2) as pool:
        monitor = pool.submit(feedback.drain)
        try:
            assert entered.wait(3)
            lifecycle = pool.submit(barrier)
            assert requested.wait(3)
            assert not finished.wait(0.05)
        finally:
            release.set()
        monitor.result(timeout=3)
        lifecycle.result(timeout=3)
    assert [kind for kind, _ in events] == ["logs", "progress", "finished"]


@pytest.mark.parametrize("cancel", [False, True])
def test_worker_flushes_burst_before_source_and_task_completion_with_legacy_factory(cancel):
    events, constructed = [], []
    tasks = (_task("A"),)
    request = SimpleNamespace(config=LLMConfig(retrieval_enabled=False), request_budget=AiRequestBudget(1))

    class Executor:
        def __init__(self, actual_request, **kwargs):
            assert actual_request is request
            assert "project_progress" not in kwargs
            constructed.append(kwargs)
            self.progress, self.log = kwargs["progress"], kwargs["log"]
            self.stop = kwargs["stop_event"]

        def execute(self, task):
            for current in range(1, 501):
                self.progress(task.key, "翻译", current, 500, "正在翻译")
                self.log(task.key, f"翻译进度 {current}")
            if cancel:
                worker.stop()
            self.log(task.key, "已取消任务" if cancel else "处理结束")
            return SourceOutcome(task, cancelled=self.stop.is_set())

    worker = AiTaskWorker(request, tasks, executor_factory=Executor)
    direct = Qt.ConnectionType.DirectConnection
    worker.source_started.connect(lambda key: events.append(("started", key)), direct)
    worker.progress.connect(lambda *args: events.append(("progress", args)), direct)
    worker.log_batch.connect(lambda rows: events.append(("logs", rows)), direct)
    worker.source_finished.connect(lambda key: events.append(("finished", key)), direct)
    worker.completed.connect(lambda outcomes: events.append(("completed", outcomes)), direct)
    worker.run()
    assert len(constructed) == 1
    assert events[0] == ("started", "A")
    assert [args[2] for kind, args in events if kind == "progress"][-1] == 500
    lines = [text for kind, rows in events if kind == "logs" for _, text in rows]
    assert lines[-1] == ("已取消任务" if cancel else "处理结束")
    assert all(len(rows) <= 100 for kind, rows in events if kind == "logs")
    assert [kind for kind, _ in events][-2:] == ["finished", "completed"]
    assert events[-1][1][0].cancelled is cancel


def test_worker_project_phase_tail_precedes_source_start_and_closure_precedes_completion(monkeypatch):
    events = []
    task = _task("A")
    request = SimpleNamespace(config=LLMConfig(retrieval_enabled=False), request_budget=AiRequestBudget(1))

    def prepare(executor, _tasks):
        executor.project_progress(_project("scan", 1))
        executor.project_progress(_project("scan", 10))
        executor.project_progress(_project("done", 10))

    def close(executor, outcomes):
        executor.progress("A", "term_closure", 1, 1, "术语检查完成")
        executor.log("A", "术语检查完成")
        return outcomes

    monkeypatch.setattr(SourceExecutor, "prepare_names", prepare)
    monkeypatch.setattr(SourceExecutor, "execute", lambda _executor, task: SourceOutcome(task))
    monkeypatch.setattr(SourceExecutor, "close_terms", close)
    worker = AiTaskWorker(request, (task,))
    direct = Qt.ConnectionType.DirectConnection
    worker.project_name_progress.connect(lambda event: events.append(("project", event.phase)), direct)
    worker.source_started.connect(lambda key: events.append(("started", key)), direct)
    worker.source_finished.connect(lambda key: events.append(("finished", key)), direct)
    worker.progress.connect(lambda *args: events.append(("progress", args[1])), direct)
    worker.log_batch.connect(lambda rows: events.append(("logs", rows)), direct)
    worker.completed.connect(lambda _: events.append(("completed", "")), direct)
    worker.run()
    assert events.index(("project", "done")) < events.index(("started", "A"))
    assert events.index(("finished", "A")) < events.index(("progress", "term_closure"))
    assert [kind for kind, _ in events][-2:] == ["logs", "completed"]


def test_worker_pause_and_stop_are_immediate_without_waiting_for_feedback():
    request = SimpleNamespace(request_budget=AiRequestBudget(1))
    worker = AiTaskWorker(request, ())
    states, logs = [], []
    worker.pause_state_changed.connect(states.append, Qt.ConnectionType.DirectConnection)
    worker.log_batch.connect(logs.append, Qt.ConnectionType.DirectConnection)
    worker._feedback.log("A", "开始翻译")
    worker.pause()
    assert worker.is_paused and states == ["pausing"]
    worker.resume()
    assert not worker.is_paused and states == ["pausing", "running"]
    worker.stop()
    assert worker.was_cancelled and not worker.is_paused
    assert logs == []
