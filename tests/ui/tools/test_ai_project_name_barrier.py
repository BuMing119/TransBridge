from __future__ import annotations

import threading
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


def _task(source, *, polish=False):
    entry = TranslationEntry(
        "same-id",
        "same-key",
        "Riverwood",
        "旧译" if polish else "",
        int(polish),
        "CELL:FULL",
        entry_key=EntryKey(SourceNamespace(source), "same-key"),
    )
    return SourceTask(
        source,
        "Shared.esp",
        None,
        TranslationEntryCollection([entry]),
        () if polish else (entry,),
        (entry,) if polish else (),
    )


def _success(task):
    translated = {
        entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", "溪木镇", 1)
        for entry in task.translate_entries
    }
    polished = {
        ai_entry_id(entry): SimpleNamespace(
            accepted=True,
            confidence=1.0,
            verdict="pass",
            processing_status="completed",
            polished_translation="溪木镇",
            candidate_translation="溪木镇",
            target_stage=1,
            note="",
        )
        for entry in task.polish_entries
    }
    return SourceOutcome(
        task,
        translation=SimpleNamespace(entry_outcomes=translated) if translated else None,
        polish=polished,
    )


@pytest.fixture
def make_worker(monkeypatch):
    workers = []

    def create(prepare, execute=None):
        tasks = (_task("translate-first"), _task("polish-only", polish=True), _task("translate-last"))
        request = SimpleNamespace(
            config=LLMConfig(retrieval_enabled=False, mixed_execution_order="parallel"),
            request_budget=AiRequestBudget(2),
        )
        state = SimpleNamespace(
            tasks=tasks,
            request=request,
            started=[],
            executed=[],
            prepared=[],
            closed=[],
            completed=[],
            snapshots=[],
        )

        def prepare_names(executor, actual_tasks):
            assert isinstance(executor, SourceExecutor)
            assert executor.request is request
            assert executor.batch_executor is not None
            assert tuple(actual_tasks) == tasks
            state.prepared.append(executor)
            prepare(executor, actual_tasks)

        def run_source(executor, task):
            state.executed.append(task.key)
            return execute(executor, task) if execute is not None else _success(task)

        def close_terms(executor, outcomes):
            assert executor is state.prepared[0]
            state.closed.append(tuple(outcomes))
            return tuple(outcomes)

        monkeypatch.setattr(SourceExecutor, "prepare_names", prepare_names)
        monkeypatch.setattr(SourceExecutor, "execute", run_source)
        monkeypatch.setattr(SourceExecutor, "close_terms", close_terms)
        worker = AiTaskWorker(request, tasks)
        worker.source_started.connect(state.started.append, Qt.ConnectionType.DirectConnection)
        worker.completed.connect(state.completed.append, Qt.ConnectionType.DirectConnection)
        worker.request_state_changed.connect(state.snapshots.append, Qt.ConnectionType.DirectConnection)
        state.worker = worker
        workers.append(worker)
        return state

    yield create
    for worker in workers:
        if worker.isRunning():
            worker.stop()
            assert worker.wait(5000)


def _entry_results(state):
    results = TaskEntryResults(state.tasks, "project-names")
    results.ingest(state.completed[0], tuple(results.entries), cancelled=state.worker.was_cancelled)
    return results


def test_all_sources_including_polish_only_wait_for_shared_name_preparation(make_worker):
    entered, release, prepared = (threading.Event() for _ in range(3))

    def prepare(executor, _tasks):
        entered.set()
        assert release.wait(5)
        executor.shared_terms["Riverwood"] = "溪木镇"
        prepared.set()

    def execute(executor, task):
        assert prepared.is_set()
        assert executor.shared_terms == {"Riverwood": "溪木镇"}
        return _success(task)

    state = make_worker(prepare, execute)
    state.worker.start()
    try:
        assert entered.wait(3)
        assert state.started == []
        assert state.executed == []
        assert state.completed == []
    finally:
        release.set()
        assert state.worker.wait(5000)

    assert len(state.prepared) == len(state.completed) == len(state.closed) == 1
    assert set(state.executed) == set(state.started) == {task.key for task in state.tasks}
    assert [outcome.task.key for outcome in state.completed[0]] == [task.key for task in state.tasks]
    assert all(outcome.successful for outcome in state.completed[0])
    assert len(_entry_results(state).ready_keys) == 3


@pytest.mark.parametrize("resume", [False, True])
def test_pause_at_name_barrier_blocks_source_dispatch_until_resume_or_stop(make_worker, resume):
    entered, release, waiting = (threading.Event() for _ in range(3))

    class ObservedPause(threading.Event):
        def wait(self, timeout=None):
            if not self.is_set():
                waiting.set()
            return super().wait(timeout)

    def prepare(_executor, _tasks):
        entered.set()
        assert release.wait(5)

    state = make_worker(prepare)
    state.worker._pause = ObservedPause()
    state.worker._pause.set()
    state.worker.start()
    try:
        assert entered.wait(3)
        state.worker.pause()
        release.set()
        assert waiting.wait(3)
        assert state.worker.is_paused
        assert state.started == state.executed == []
        state.worker.resume() if resume else state.worker.stop()
        assert state.worker.wait(5000)
    finally:
        release.set()
        if state.worker.isRunning():
            state.worker.stop()
            assert state.worker.wait(5000)

    results = _entry_results(state)
    assert state.worker.was_cancelled is not resume
    if resume:
        assert set(state.executed) == {task.key for task in state.tasks}
        assert len(results.ready_keys) == 3
    else:
        assert state.started == state.executed == []
        assert not results.ready_keys
        assert {row.status for row in results.entries.values()} == {"not_started"}


def test_cancel_during_name_call_drains_shared_budget_and_never_starts_sources(make_worker):
    admitted, release = threading.Event(), threading.Event()

    def prepare(executor, _tasks):
        with executor.request.request_budget.acquire(
            cancel_event=executor.stop, pause_event=executor.pause, lane_key="project-names"
        ):
            admitted.set()
            assert release.wait(5)

    state = make_worker(prepare)
    state.worker.start()
    try:
        assert admitted.wait(3)
        state.worker.stop()
        assert state.worker.isRunning()
        assert state.request.request_budget.snapshot().in_flight == 1
        assert state.started == state.executed == []
    finally:
        release.set()
        assert state.worker.wait(5000)

    assert state.worker.was_cancelled
    assert len(state.completed) == 1
    assert state.started == state.executed == []
    assert state.snapshots[-1].in_flight == state.snapshots[-1].waiting == 0
    assert state.snapshots[-1].peak_in_flight == 1
    results = _entry_results(state)
    assert not results.ready_keys
    assert {row.status for row in results.entries.values()} == {"not_started"}


def test_name_preparation_exception_fails_every_source_without_application_evidence(make_worker):
    def prepare(_executor, _tasks):
        raise RuntimeError("项目名称批次初始化失败")

    state = make_worker(prepare)
    state.worker.run()

    assert len(state.completed) == 1
    assert state.started == state.executed == state.closed == []
    assert not state.worker.was_cancelled
    for outcome, task in zip(state.completed[0], state.tasks, strict=True):
        assert outcome.task is task
        assert not outcome.successful
        assert "项目名称批次初始化失败" in outcome.error
        assert outcome.failed_keys == tuple(ai_entry_key(entry) for entry in task.entries)
    results = _entry_results(state)
    assert not results.ready_keys
    assert {row.status for row in results.entries.values()} == {"failed"}
    assert [entry.translation for task in state.tasks for entry in task.entries] == ["", "旧译", ""]


def test_source_failure_after_name_barrier_does_not_discard_other_sources(make_worker):
    prepared = threading.Event()

    def prepare(_executor, _tasks):
        prepared.set()

    def execute(_executor, task):
        assert prepared.is_set()
        if task.key == "translate-first":
            raise RuntimeError("单个来源翻译失败")
        return _success(task)

    state = make_worker(prepare, execute)
    state.worker.run()

    failed, polished, translated = state.completed[0]
    assert failed.task.key == "translate-first" and not failed.successful
    assert "单个来源翻译失败" in failed.error
    assert polished.task.key == "polish-only" and polished.successful
    assert translated.task.key == "translate-last" and translated.successful
    assert len(state.closed) == 1
    results = _entry_results(state)
    assert len(results.failed_keys) == 1
    assert len(results.ready_keys) == 2
