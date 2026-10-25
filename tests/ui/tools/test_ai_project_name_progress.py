"""Project-name progress uses phase-wide denominators instead of source stages."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.ai_translator.name_preparation_plan import NameAuthority
from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_name_preparation
from transbridge.ui.tools.ai_translator.project_name_progress import ProjectNameProgressReporter
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_scope import SourceTask
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


def _task(key, names):
    entries = tuple(
        TranslationEntry(str(index), str(index), name, "", 0, "WEAP:FULL") for index, name in enumerate(names)
    )
    return SourceTask(key, key, f"{key}.esp", TranslationEntryCollection(entries), entries, ())


def _executor(monkeypatch, tmp_path=None):
    pause = Event()
    pause.set()
    events, legacy = [], []
    executor = SimpleNamespace(
        shared_term_pool=SimpleNamespace(load=lambda: None),
        config=SimpleNamespace(max_concurrent=2, get_ai_translator_dir=lambda _: str(tmp_path)),
        request=SimpleNamespace(run_id="progress", spec=SimpleNamespace(overwrite=False)),
        attempt_id=None,
        stop=Event(),
        pause=pause,
        consistency=None,
        log=lambda *_: None,
        progress=lambda *args: legacy.append(args),
        project_progress=events.append,
    )

    class Authority:
        def __init__(self, *_args, **_kwargs):
            pass

        def for_entry(self, entry, **_kwargs):
            return (
                NameAuthority(exact_translation="已有译法")
                if entry.original == "Known"
                else NameAuthority(blocked=True)
            )

    monkeypatch.setattr(task_name_preparation, "SourceNameAuthority", Authority)
    monkeypatch.setattr(
        task_name_preparation, "_manager", lambda *_: SimpleNamespace(lookup_context_for_entry=lambda _: None)
    )
    return executor, events, legacy


def test_parallel_source_progress_adds_absolute_counts_with_fixed_denominators():
    events = []
    reporter = ProjectNameProgressReporter(("A", "B", "shared"), events.append)
    reporter.begin("translate", {"A": 3, "B": 5}, "翻译名称")
    start = Barrier(2)

    def update(key, total):
        start.wait(timeout=3)
        for current in range(1, total + 1):
            reporter.update("translate", key, current=current, detail=f"[{current:3d}%] {current}/{total} | 正在翻译")
        # A retry's older counter cannot decrease settled project progress.
        reporter.update("translate", key, current=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, "A", 3), pool.submit(update, "B", 5)]
        for future in futures:
            future.result(timeout=3)
    assert [event.current for event in events] == sorted(event.current for event in events)
    assert all(event.total == 8 for event in events)
    assert all(tuple(row.total for row in event.sources) == (3, 5, 0) for event in events)
    assert events[-1].current == 8
    assert events[-1].sources[0].detail == "正在翻译"
    reporter.update("translate", "A", detail="正在整理翻译结果")
    assert reporter.snapshot.current == reporter.snapshot.total == 8


def test_cancellation_keeps_actual_progress_and_ignores_late_batches_and_finish():
    events = []
    reporter = ProjectNameProgressReporter(("A", "B"), events.append)
    reporter.begin("translate", {"A": 5, "B": 7}, "翻译名称")
    reporter.update("translate", "A", current=2)
    reporter.cancel()
    reporter.update("translate", "B", current=7)
    reporter.begin("validate", {"A": 5, "B": 7}, "校验")
    reporter.finish({"A": 5, "B": 7}, "完成")
    assert events[-1].phase == "cancelled"
    assert (events[-1].current, events[-1].total) == (2, 12)
    assert [row.current for row in events[-1].sources] == [2, 0]
    assert all(event.phase != "done" for event in events)


def test_coordinator_registers_all_sources_before_translation_and_separates_phases(monkeypatch, tmp_path):
    executor, events, legacy = _executor(monkeypatch, tmp_path)
    tasks = (
        _task("A", ("Shared", "Alpha", "Known")),
        _task("B", ("Shared", "Beta")),
        _task("C", ("Shared",)),
    )
    barrier = Barrier(2)

    def prepare(executor, task, selected):
        snapshot = executor.name_progress.snapshot
        assert snapshot.phase == "translate"
        assert {row.key: row.total for row in snapshot.sources} == {"A": 2, "B": 1, "C": 0}
        barrier.wait(timeout=3)
        executor.name_progress.update("translate", task.key, current=1, detail="第一轮")
        return {index: ("名称译文", "") for index, _ in selected}

    monkeypatch.setattr(task_name_preparation, "_prepare_source", prepare)
    cache = task_name_preparation.prepare_project_names(executor, tasks)
    phases = list(dict.fromkeys(event.phase for event in events))
    assert phases == ["scan", "load", "resolve", "reuse", "translate", "validate", "publish", "done"]
    assert {phase: next(event.total for event in events if event.phase == phase) for phase in phases} == {
        "scan": 6,
        "load": 3,
        "resolve": 6,
        "reuse": 1,
        "translate": 3,
        "validate": 3,
        "publish": 0,
        "done": 4,
    }
    translation = [event for event in events if event.phase == "translate"]
    assert translation[-1].current == 3
    assert translation[0].sources[2].detail == "共享名称由其他来源准备"
    assert (events[-1].name_count, events[-1].occurrence_count, events[-1].reused_count) == (4, 6, 1)
    assert len(cache["A"]) == 3 and len(cache["B"]) == 2 and len(cache["C"]) == 1
    assert legacy and all(len(event) == 5 and event[1] == "project_names" for event in legacy)


def test_cancelled_model_batch_does_not_jump_to_validation_or_done(monkeypatch):
    executor, events, _ = _executor(monkeypatch)

    def prepare(executor, task, selected):
        executor.name_progress.update("translate", task.key, current=1)
        executor.stop.set()
        return {index: ("", "任务已取消") for index, _ in selected}

    monkeypatch.setattr(task_name_preparation, "_prepare_source", prepare)
    cache = task_name_preparation.prepare_project_names(executor, (_task("A", ("Alpha", "Beta")),))
    assert (events[-1].phase, events[-1].current, events[-1].total) == ("cancelled", 1, 2)
    assert not {"validate", "publish", "done"}.intersection(event.phase for event in events)
    assert all(item.reason == "任务已取消" for item in cache["A"].values())


def test_no_names_finishes_without_fabricating_work_or_loading_terms(monkeypatch):
    executor, events, _ = _executor(monkeypatch)

    def unexpected(*_):
        raise AssertionError("no names must not load terminology")

    executor.shared_term_pool.load = unexpected
    cache = task_name_preparation.prepare_project_names(executor, (_task("A", ()),))
    assert cache == {}
    assert list(dict.fromkeys(event.phase for event in events)) == ["scan", "done"]
    assert all(event.current == event.total == 0 for event in events)
    assert (events[-1].current, events[-1].total, events[-1].name_count) == (0, 0, 0)


def test_name_translator_stage_callbacks_never_change_registered_name_counts(monkeypatch):
    from transbridge.ai_translator import translator

    events, legacy = [], []
    config = LLMConfig(retrieval_enabled=False)
    request = SimpleNamespace(
        config=config,
        spec=SimpleNamespace(overwrite=False),
        run_id="progress",
        request_budget=AiRequestBudget(2),
        terminology_binding=ProjectTerminologyBinding(),
    )
    pause = Event()
    pause.set()
    executor = SourceExecutor(
        request,
        stop_event=Event(),
        pause_event=pause,
        shared_terms={},
        terms_lock=Lock(),
        progress=lambda *args: legacy.append(args),
        log=lambda *_: None,
        project_progress=events.append,
    )
    executor.name_progress = ProjectNameProgressReporter(("A", "B"), events.append)
    executor.name_progress.begin("translate", {"A": 3, "B": 2}, "翻译名称")

    class Translator:
        def __init__(self, *_args, **_kwargs):
            pass

        def translate(self, *, progress_callback, stage_progress_callback, **_kwargs):
            progress_callback(2, 3, "[ 66%] 2/3 | 第一轮", 2, 0, 0)
            for stage in ("terms", "commit_translation", "finalize"):
                stage_progress_callback(stage, 100, 100, f"{stage} 已完成")
            return SimpleNamespace()

    monkeypatch.setattr(translator, "AutoTranslator", Translator)
    executor._translate(_task("A", ("Alpha", "Beta", "Gamma")), object(), name_preparation=True)
    assert all(event.total == 5 for event in events)
    assert [event.current for event in events] == [0, 2, 2, 2, 2]
    assert events[-1].sources[0].total == 3
    assert events[-1].sources[0].detail == "finalize 已完成"
    assert all(event[1] == "project_names" for event in legacy)


@pytest.mark.parametrize("real_executor", [False, True])
def test_worker_forwards_project_and_source_finished_signals_without_expanding_factory_api(monkeypatch, real_executor):
    events = []
    task = _task("A", ("Alpha",))
    request = SimpleNamespace(config=LLMConfig(retrieval_enabled=False), request_budget=AiRequestBudget(1))

    def prepare(executor, _tasks):
        reporter = ProjectNameProgressReporter((task.key,), executor.project_progress)
        reporter.begin("scan", {task.key: 1}, "统计")
        reporter.finish({task.key: 1}, "已完成")

    def close(_executor, outcomes):
        assert ("source_finished", task.key) in events
        return outcomes

    class LegacyExecutor:
        def __init__(self, _request, **kwargs):
            assert "project_progress" not in kwargs

        def execute(self, task):
            return SourceOutcome(task)

    monkeypatch.setattr(SourceExecutor, "prepare_names", prepare)
    monkeypatch.setattr(SourceExecutor, "execute", lambda _executor, task: SourceOutcome(task))
    monkeypatch.setattr(SourceExecutor, "close_terms", close)
    worker = AiTaskWorker(request, (task,), executor_factory=SourceExecutor if real_executor else LegacyExecutor)
    worker.project_name_progress.connect(
        lambda event: events.append(("project", event.phase)), Qt.ConnectionType.DirectConnection
    )
    worker.source_finished.connect(
        lambda key: events.append(("source_finished", key)), Qt.ConnectionType.DirectConnection
    )
    worker.completed.connect(lambda _: events.append(("completed", "")), Qt.ConnectionType.DirectConnection)
    worker.run()
    assert events[-2:] == [("source_finished", "A"), ("completed", "")]
    assert [event for event in events if event[0] == "project"] == (
        [("project", "scan"), ("project", "done")] if real_executor else []
    )
