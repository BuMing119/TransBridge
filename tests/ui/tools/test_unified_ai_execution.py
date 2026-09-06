from __future__ import annotations

from dataclasses import replace
import threading
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ActionRule
from transbridge.ui.tools.ai_translator import source_execution
from transbridge.ui.tools.ai_translator.scope_presenter import TranslationScope
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_scope import SourceTask, SourceWorkbenchScope, TaskScope
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


def _entry(source="first", *, key="key", legacy_id="same-id", translation="", stage=0):
    return TranslationEntry(
        legacy_id,
        key,
        f"Original {key}",
        translation,
        stage,
        "NPC_:FULL",
        entry_key=EntryKey(SourceNamespace(source), key),
    )


def _slot(source, entries):
    return SimpleNamespace(label=source, esp_path=f"{source}.esp", collection=TranslationEntryCollection(entries))


def _task(source="first", *, mixed=False):
    translate = _entry(source)
    polish = _entry(source, key="polish", legacy_id="polish-id", translation="旧译", stage=1)
    entries = (translate, polish) if mixed else (translate,)
    return SourceTask(
        source, source, f"{source}.esp", TranslationEntryCollection(entries), (translate,), (polish,) if mixed else ()
    )


def _request(order="serial", budget=1):
    return SimpleNamespace(
        config=LLMConfig(mixed_execution_order=order),
        request_budget=AiRequestBudget(budget),
    )


@pytest.mark.parametrize("preset", ["selection", "table_view"])
def test_table_shortcuts_are_captured_and_never_cross_sources_with_same_legacy_id(preset):
    first, second = _slot("first", [_entry()]), _slot("second", [_entry("second")])
    ctx = SimpleNamespace(slots={"first": first, "second": second}, active_slot=first, entry_labels={})
    scope = TaskScope(ctx, SourceWorkbenchScope(tuple(first.collection), ("same-id",)), lambda e: e.context)
    ctx.active_slot = second
    state = TranslationScope(preset=preset, selected_entry_ids=frozenset({"same-id"}))

    tasks = scope.build((second, first), state, mode="translate", config=LLMConfig(), overwrite=False)

    assert tasks[0].entries == ()
    assert [e.identity.namespace.value for e in tasks[1].entries] == ["first"]
    assert tasks[1].entries[0].identity != tuple(second.collection)[0].identity


@pytest.mark.parametrize(
    ("mode", "overwrite", "expected_translate", "expected_polish"),
    [
        ("translate", False, {"new", "stale"}, set()),
        ("translate", True, {"new", "stale", "done"}, set()),
        ("polish", False, set(), {"stale", "done"}),
        ("mixed", False, {"new", "stale"}, {"done"}),
    ],
)
def test_every_mode_uses_same_stage_policy_and_source_candidate_selection(
    mode,
    overwrite,
    expected_translate,
    expected_polish,
):
    entries = [
        _entry(key="new", legacy_id="new"),
        _entry(key="stale", legacy_id="stale", translation="旧译", stage=0),
        _entry(key="done", legacy_id="done", translation="完成", stage=1),
        _entry(key="locked", legacy_id="locked", stage=STAGE_LOCKED),
        _entry(key="hidden", legacy_id="hidden", stage=STAGE_HIDDEN),
    ]
    first = _slot("first", entries)
    ctx = SimpleNamespace(slots={"first": first}, active_slot=first, entry_labels={})
    config = LLMConfig(
        action_rules=[
            ActionRule(priority=0, status_filter={0}, action="translate"),
            ActionRule(priority=1, status_filter={1}, action="polish"),
        ]
    )
    scope = TaskScope(ctx, SourceWorkbenchScope(), lambda e: e.context)

    (task,) = scope.build((first,), TranslationScope(), mode=mode, config=config, overwrite=overwrite)

    assert {e.key for e in task.translate_entries} == expected_translate
    assert {e.key for e in task.polish_entries} == expected_polish


def test_scope_rejects_replaced_source_instead_of_running_stale_collection():
    first = _slot("first", [_entry()])
    ctx = SimpleNamespace(slots={"first": first}, active_slot=first, entry_labels={})
    scope = TaskScope(ctx, SourceWorkbenchScope(), lambda e: e.context)
    ctx.slots["first"] = _slot("replacement", [_entry("replacement")])
    with pytest.raises(ValueError, match="处理来源已变化"):
        scope.build((first,), TranslationScope(), mode="translate", config=LLMConfig(), overwrite=False)


@pytest.mark.parametrize("source_count", [1, 3])
def test_worker_runs_same_executor_budget_and_shared_terms_for_each_source(source_count):
    request = _request()
    tasks = tuple(_task(f"source-{i}") for i in range(source_count))
    seen = []

    class Executor:
        def __init__(self, actual_request, **kwargs):
            assert actual_request is request
            self.request = actual_request
            self.shared = kwargs["shared_terms"]
            self.stop, self.pause = kwargs["stop_event"], kwargs["pause_event"]

        def execute(self, task):
            with self.request.request_budget.acquire(cancel_event=self.stop, pause_event=self.pause):
                seen.append((self, task.key, tuple(self.shared)))
                self.shared[task.key] = f"术语-{task.key}"
                return SourceOutcome(task, translation={task.entries[0].identity: f"译文-{task.key}"})

    worker = AiTaskWorker(request, tasks, executor_factory=Executor)
    completed = []
    worker.completed.connect(completed.append)
    worker.run()

    (outcomes,) = completed
    assert [o.task.key for o in outcomes] == [task.key for task in tasks]
    assert len({id(item[0]) for item in seen}) == 1
    assert seen[-1][2] == tuple(task.key for task in tasks[:-1])
    assert [o.translation[o.task.entries[0].identity] for o in outcomes] == [f"译文-{t.key}" for t in tasks]
    assert request.request_budget.snapshot().peak_in_flight == 1
    assert request.request_budget.snapshot().in_flight == 0


def test_worker_continues_after_failed_source_and_preserves_failed_identity():
    tasks = (_task("first"), _task("second"))

    class Executor:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, task):
            if task.key == "first":
                return SourceOutcome(task, error="服务不可用", failed_keys=(task.entries[0].key,))
            return SourceOutcome(task, translation={task.entries[0].identity: "第二个成功"})

    worker = AiTaskWorker(_request(), tasks, executor_factory=Executor)
    completed = []
    worker.completed.connect(completed.append)
    worker.run()
    first, second = completed[0]
    assert not first.successful and first.failed_keys == (tasks[0].entries[0].key,)
    assert second.successful and second.translation[tasks[1].entries[0].identity] == "第二个成功"


@pytest.mark.parametrize("resume", [False, True])
def test_worker_pause_gates_execution_and_stop_unblocks_paused_worker(resume):
    waiting = threading.Event()
    executed = []

    class ObservableEvent:
        def __init__(self):
            self.event = threading.Event()

        def set(self):
            self.event.set()

        def clear(self):
            self.event.clear()

        def is_set(self):
            return self.event.is_set()

        def wait(self):
            waiting.set()
            return self.event.wait(2)

    class Executor:
        def __init__(self, *_args, **_kwargs):
            pass

        def execute(self, task):
            executed.append(task.key)
            return SourceOutcome(task, translation={task.entries[0].identity: "已完成"})

    worker = AiTaskWorker(_request(), (_task(),), executor_factory=Executor)
    worker._pause = ObservableEvent()
    worker.pause()
    completed = []
    worker.completed.connect(completed.append, Qt.ConnectionType.DirectConnection)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert waiting.wait(2)
        assert worker.is_paused and executed == []
        worker.resume() if resume else worker.stop()
    finally:
        thread.join(2)
        if thread.is_alive():
            worker.stop()
            thread.join(2)
    assert not thread.is_alive()
    assert executed == (["first"] if resume else [])
    assert len(completed[0]) == int(resume)
    assert worker.was_cancelled is not resume


@pytest.fixture
def make_executor(monkeypatch, tmp_path):
    store_class = source_execution.WorkflowLogStore
    monkeypatch.setattr(
        source_execution,
        "WorkflowLogStore",
        lambda path, **kwargs: store_class(path, log_base=tmp_path, **kwargs),
    )

    def make(order="serial", budget=1):
        request = _request(order, budget)
        pause = threading.Event()
        pause.set()
        return SourceExecutor(
            request,
            stop_event=threading.Event(),
            pause_event=pause,
            shared_terms={},
            terms_lock=threading.Lock(),
            progress=lambda *_: None,
            log=lambda *_: None,
        )

    return make


@pytest.mark.parametrize("order", ["serial", "parallel"])
def test_source_executor_preserves_both_stage_products_and_shares_budget(make_executor, monkeypatch, order):
    executor = make_executor(order)
    task = _task(mixed=True)
    translate_product = SimpleNamespace(
        failed_count=0, failed_entries=[], translations={task.entries[0].identity: "新译"}
    )
    polish_product = {task.polish_entries[0].id: SimpleNamespace(verdict="accepted", translation="润色译文")}
    visited = []
    parallel_stages = threading.Barrier(2) if order == "parallel" else None

    def translate(_task, _store):
        if parallel_stages is not None:
            parallel_stages.wait(timeout=2)
        with executor.request.request_budget.acquire(cancel_event=executor.stop, pause_event=executor.pause):
            visited.append("translate")
            return translate_product

    def polish(_task, _store):
        if parallel_stages is not None:
            parallel_stages.wait(timeout=2)
        with executor.request.request_budget.acquire(cancel_event=executor.stop, pause_event=executor.pause):
            visited.append("polish")
            return polish_product

    monkeypatch.setattr(executor, "_translate", translate)
    monkeypatch.setattr(executor, "_polish", polish)
    outcome = executor.execute(task)
    assert outcome.successful
    assert outcome.translation.translations[task.entries[0].identity] == "新译"
    assert outcome.polish[task.polish_entries[0].id].translation == "润色译文"
    assert sorted(visited) == ["polish", "translate"]
    if order == "serial":
        assert visited == ["translate", "polish"]
    assert executor.request.request_budget.snapshot().peak_in_flight == 1


@pytest.mark.parametrize("order", ["serial", "parallel"])
def test_cancelled_source_does_not_start_stages_or_report_success(make_executor, monkeypatch, order):
    executor = make_executor(order)
    task = _task(mixed=order == "parallel")
    executor.stop.set()
    started = []
    monkeypatch.setattr(executor, "_translate", lambda *_: started.append("translate"))
    monkeypatch.setattr(executor, "_polish", lambda *_: started.append("polish") or {})

    outcome = executor.execute(task)
    assert started == []
    assert not outcome.successful


def test_translation_failure_diagnostics_resolve_to_real_entry_keys(make_executor, monkeypatch):
    executor = make_executor()
    task = _task()
    monkeypatch.setattr(
        executor,
        "_translate",
        lambda *_: SimpleNamespace(
            failed_count=1,
            failed_entries=[f"{task.entries[0].id}: 模型未返回有效译文"],
        ),
    )
    outcome = executor.execute(task)
    assert not outcome.successful
    assert outcome.failed_keys == (task.entries[0].key,)


@pytest.mark.parametrize("order", ["serial", "parallel"])
def test_stage_exception_marks_source_failed_without_losing_translation_product(make_executor, monkeypatch, order):
    executor = make_executor(order)
    task = _task(mixed=True)
    product = SimpleNamespace(failed_count=0, failed_entries=[], translations={task.entries[0].identity: "新译"})
    monkeypatch.setattr(executor, "_translate", lambda *_: product)

    def fail(*_):
        raise RuntimeError("校对服务不可用")

    monkeypatch.setattr(executor, "_polish", fail)
    outcome = executor.execute(task)
    assert not outcome.successful
    assert outcome.translation.translations[task.entries[0].identity] == "新译"
    assert set(outcome.failed_keys) == {entry.key for entry in task.entries}
    assert outcome.error


def test_missing_polish_result_is_a_retryable_failed_key(make_executor, monkeypatch):
    executor = make_executor()
    task = replace(_task(mixed=True), translate_entries=())
    monkeypatch.setattr(executor, "_polish", lambda *_: {})
    outcome = executor.execute(task)
    assert not outcome.successful
    assert outcome.failed_keys == (task.polish_entries[0].key,)


def test_real_stage_adapters_pass_same_budget_events_and_exact_targets(make_executor, monkeypatch):
    from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

    executor = make_executor()
    task = _task(mixed=True)
    executor.request.run_id = "test-run"
    executor.request.spec = SimpleNamespace(
        overwrite=False,
        execution_profile=AiExecutionProfile.from_config("mixed", executor.config),
    )
    executor.request.terminology_binding = SimpleNamespace(translator_kwargs=lambda: {})
    adapters = []

    class Translator:
        def __init__(self, _config, _client, _project_id, **kwargs):
            self.budget = kwargs["request_budget"]
            assert kwargs["shared_in_flight_terms"] is executor.shared_terms
            assert kwargs["shared_in_flight_lock"] is executor.terms_lock

        def translate(self, **kwargs):
            assert kwargs["collection"] is task.collection
            assert kwargs["target_entry_ids"] == [entry.key for entry in task.translate_entries]
            assert kwargs["stop_event"] is executor.stop
            assert kwargs["pause_event"] is executor.pause
            with self.budget.acquire(cancel_event=kwargs["stop_event"], pause_event=kwargs["pause_event"]):
                adapters.append(self.budget)
                return SimpleNamespace(failed_count=0, failed_entries=[], translated_keys=kwargs["target_entry_ids"])

    def build_pipeline(_config, _path, **kwargs):
        assert kwargs["stop_event"] is executor.stop
        assert kwargs["pause_event"] is executor.pause
        assert kwargs["terminology_binding"] is executor.request.terminology_binding

        def process(entries, **process_kwargs):
            assert entries == list(task.polish_entries)
            assert process_kwargs["stop_event"] is executor.stop
            assert process_kwargs["pause_event"] is executor.pause
            with kwargs["request_budget"].acquire():
                adapters.append(kwargs["request_budget"])
                return {
                    entry.id: SimpleNamespace(verdict="accepted", translation=f"校对-{entry.key}") for entry in entries
                }

        return SimpleNamespace(process=process)

    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", Translator)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.proofread_composition.build_proofread_pipeline",
        build_pipeline,
    )
    outcome = executor.execute(task)
    assert outcome.successful
    assert outcome.translation.translated_keys == [task.translate_entries[0].key]
    assert outcome.polish[task.polish_entries[0].id].translation == "校对-polish"
    assert adapters == [executor.request.request_budget, executor.request.request_budget]


def test_stop_between_serial_stages_preserves_translation_and_skips_polish(make_executor, monkeypatch):
    executor = make_executor()
    task = _task(mixed=True)
    visited = []

    def translate(*_):
        executor.stop.set()
        return SimpleNamespace(failed_count=0, failed_entries=[], translated_keys=[task.translate_entries[0].key])

    monkeypatch.setattr(executor, "_translate", translate)
    monkeypatch.setattr(executor, "_polish", lambda *_: visited.append("polish"))
    outcome = executor.execute(task)
    assert visited == []
    assert not outcome.successful
    assert outcome.translation.translated_keys == [task.translate_entries[0].key]
    assert task.polish_entries[0].key in outcome.failed_keys


@pytest.mark.parametrize("order", ["serial", "parallel"])
def test_missing_translation_stage_product_fails_closed(make_executor, monkeypatch, order):
    executor = make_executor(order)
    task = _task(mixed=True)
    monkeypatch.setattr(executor, "_translate", lambda *_: None)
    monkeypatch.setattr(
        executor, "_polish", lambda *_: {task.polish_entries[0].id: SimpleNamespace(verdict="accepted")}
    )
    outcome = executor.execute(task)
    assert not outcome.successful
    assert "翻译阶段未返回结果" in outcome.error
    assert set(outcome.failed_keys) == {entry.key for entry in task.entries}


@pytest.mark.parametrize("decision", ["accepted", "rejected", "pending", "failed"])
def test_source_report_uses_explicit_decisions_including_unchanged_acceptance(monkeypatch, decision):
    from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter

    task = replace(_task(mixed=True), translate_entries=())
    entry = task.polish_entries[0]
    candidate = SimpleNamespace(
        original_translation=entry.translation, polished_translation=entry.translation, confidence=1, accepted=True
    )
    outcome = SourceOutcome(task, polish={entry.id: candidate})
    if decision in {"accepted", "rejected"}:
        outcome.polish_summary = ResultPresenter().apply_decisions(
            task.collection,
            list(task.polish_entries),
            {entry.id: entry.translation if decision == "accepted" else None},
            results=outcome.polish,
        )
    elif decision == "failed":
        outcome.failed_keys = (entry.key,)
    monkeypatch.setattr("transbridge.ui.tools.ai_translator.reporting.render_translation_report", lambda *_: None)
    source_execution.render_source_report(outcome, SimpleNamespace(run_id="report", config=LLMConfig()))
    snapshot = outcome.snapshot
    assert dict(snapshot.candidates[0].report_details)["result_status"] == decision
    assert snapshot.accepted_count == int(decision == "accepted")
    assert snapshot.failure_count == int(decision == "failed")
    assert task.collection.get(entry.identity).translation == entry.translation
    if decision == "pending":
        assert snapshot.run_spec_summary["polish_counts"] == {
            "accepted": 0,
            "rejected": 0,
            "failed": 0,
            "pending": 1,
        }
        assert not any(d.code == "POLISH_ENTRY_REJECTED" for d in snapshot.diagnostics)
