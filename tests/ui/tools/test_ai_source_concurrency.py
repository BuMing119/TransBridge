from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.ui.tools.ai_translator import source_execution
from transbridge.ui.tools.ai_translator.source_dispatch import run_sources
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.source_terms import source_run_id
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


def _wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def _task(tmp_path: Path, key: str, *, mixed=False) -> SourceTask:
    def entry(name: str, translation="", stage=0):
        return TranslationEntry(
            name,
            name,
            f"Original {name}",
            translation,
            stage,
            "NPC_:FULL",
            entry_key=EntryKey(SourceNamespace(key), name),
        )

    translate = entry("translate")
    polish = entry("polish", "旧译", 1)
    entries = (translate, polish) if mixed else (translate,)
    return SourceTask(
        key,
        "Shared.esp",
        str(tmp_path / key / "Shared.esp"),
        TranslationEntryCollection(entries),
        (translate,),
        (polish,) if mixed else (),
    )


def _events():
    stop, pause = threading.Event(), threading.Event()
    pause.set()
    return stop, pause


def test_source_finished_notification_arrives_before_other_sources_finish(tmp_path):
    stop, pause = _events()
    release, fast_finished = threading.Event(), threading.Event()
    tasks = (_task(tmp_path, "slow"), _task(tmp_path, "fast"))
    notified = []

    class Executor:
        def execute(self, task):
            if task.key == "slow":
                assert release.wait(3)
            return SourceOutcome(task)

    def finished(key):
        notified.append(key)
        if key == "fast":
            fast_finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            run_sources,
            tasks,
            Executor(),
            stop_event=stop,
            pause_event=pause,
            started=lambda _: None,
            max_concurrent=2,
            finished=finished,
        )
        try:
            assert fast_finished.wait(2)
            assert notified == ["fast"]
            assert not pending.done()
        finally:
            release.set()
        outcomes = pending.result(timeout=3)
    assert notified == ["fast", "slow"]
    assert tuple(outcome.task.key for outcome in outcomes) == ("slow", "fast")


class _TrackingProvider:
    def __init__(self, operation):
        self.operation = operation
        self.active = set()
        self.calls = []
        self.peak = 0
        self.lock = threading.Lock()

    def chat(self, messages, max_tokens=0):
        token = messages[0]["content"]
        with self.lock:
            self.calls.append(token)
            self.active.add(token)
            self.peak = max(self.peak, len(self.active))
        try:
            self.operation(token)
            return f"translated:{token}"
        finally:
            with self.lock:
                self.active.remove(token)

    def cancel(self):
        pass


def test_source_can_proofread_while_another_translates_under_one_real_budget(tmp_path, monkeypatch):
    budget = AiRequestBudget(2)
    request = SimpleNamespace(config=LLMConfig(mixed_execution_order="serial", retrieval_enabled=False))
    request.request_budget = budget
    stop, pause = _events()
    translating, proofreading = threading.Event(), threading.Event()
    release_translation, release_proofread = threading.Event(), threading.Event()
    finished_fast = threading.Event()
    # Completion order is deliberately opposite to source order.
    slow, fast = _task(tmp_path, "slow"), _task(tmp_path, "fast", mixed=True)
    store_class = source_execution.WorkflowLogStore
    monkeypatch.setattr(
        source_execution,
        "WorkflowLogStore",
        lambda path, **kwargs: store_class(path, log_base=tmp_path / "logs", **kwargs),
    )

    def provider_call(token):
        if token == "slow:translation":
            translating.set()
            assert release_translation.wait(3)
        elif token == "fast:proofread":
            assert translating.wait(2)
            proofreading.set()
            assert release_proofread.wait(3)

    provider = _TrackingProvider(provider_call)
    executor = SourceExecutor(
        request,
        stop_event=stop,
        pause_event=pause,
        shared_terms={},
        terms_lock=threading.Lock(),
        progress=lambda *_: None,
        log=lambda *_: None,
    )

    def call(task, stage):
        client = LimitedLLMClient(provider, budget, cancel_event=stop, pause_event=pause, lane_key=(task.key, stage))
        return client.chat([{"content": f"{task.key}:{stage}"}])

    def translate(task, _store):
        result = call(task, "translation")
        return SimpleNamespace(failed_count=0, failed_entries=[], translations={task.entries[0].identity: result})

    def proofread(task, _store):
        result = call(task, "proofread")
        return {task.polish_entries[0].id: SimpleNamespace(verdict="accepted", translation=result)}

    original_execute = executor.execute

    def execute(task):
        result = original_execute(task)
        if task.key == "fast":
            finished_fast.set()
        return result

    monkeypatch.setattr(executor, "_translate", translate)
    monkeypatch.setattr(executor, "_polish", proofread)
    monkeypatch.setattr(executor, "execute", execute)
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run_sources,
            (slow, fast),
            executor,
            stop_event=stop,
            pause_event=pause,
            started=lambda _: None,
            max_concurrent=2,
        )
        try:
            assert proofreading.wait(2)
            assert provider.active == {"slow:translation", "fast:proofread"}
            assert budget.snapshot().in_flight == 2
            release_proofread.set()
            assert finished_fast.wait(2)
            assert not future.done()
            assert budget.snapshot().in_flight == 1
        finally:
            release_proofread.set()
            release_translation.set()
        outcomes = future.result(timeout=2)

    assert [outcome.task.key for outcome in outcomes] == ["slow", "fast"]
    assert all(outcome.successful for outcome in outcomes)
    assert outcomes[1].polish[fast.polish_entries[0].id].translation == "translated:fast:proofread"
    assert provider.peak == budget.snapshot().peak_in_flight == 2
    assert budget.snapshot().in_flight == 0
    assert all(Path(outcome.log_dir).is_relative_to(tmp_path) for outcome in outcomes)


def test_source_burst_cannot_starve_another_sources_proofread_lane(tmp_path):
    budget = AiRequestBudget(1)
    stop, pause = _events()
    occupied = budget.acquire()
    burst_queued = threading.Event()
    provider = _TrackingProvider(lambda _: None)
    clients = {
        "A": LimitedLLMClient(provider, budget, lane_key=("A", "translate")),
        "B": LimitedLLMClient(provider, budget, lane_key=("B", "proofread")),
    }

    class Executor:
        def execute(self, task):
            if task.key == "A":
                with ThreadPoolExecutor(max_workers=3) as pool:
                    pending = []
                    for index in range(1, 4):
                        pending.append(pool.submit(clients["A"].chat, [{"content": f"A{index}"}]))
                        _wait_until(lambda: budget.snapshot().waiting == index)
                    burst_queued.set()
                    product = tuple(future.result(timeout=2) for future in pending)
            else:
                assert burst_queued.wait(2)
                product = clients["B"].chat([{"content": "B-proofread"}])
            return SourceOutcome(task, translation=product)

    tasks = (_task(tmp_path, "A"), _task(tmp_path, "B"))
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run_sources,
            tasks,
            Executor(),
            stop_event=stop,
            pause_event=pause,
            started=lambda _: None,
            max_concurrent=1,
        )
        try:
            _wait_until(lambda: budget.snapshot().waiting == 4)
            assert provider.calls == []
        finally:
            occupied.release()
        outcomes = future.result(timeout=3)
    assert provider.calls == ["A1", "B-proofread", "A2", "A3"]
    assert [outcome.task.key for outcome in outcomes] == ["A", "B"]
    assert all(outcome.successful for outcome in outcomes)
    assert budget.snapshot().peak_in_flight == 1


def test_stop_cancels_waiting_request_and_unscheduled_sources_but_drains_active_call(tmp_path):
    budget = AiRequestBudget(1)
    stop, pause = _events()
    admitted, release = threading.Event(), threading.Event()
    started = []

    def provider_call(_token):
        admitted.set()
        assert release.wait(3)

    provider = _TrackingProvider(provider_call)

    class Executor:
        def execute(self, task):
            if task.key == "B":
                assert admitted.wait(2)
            client = LimitedLLMClient(provider, budget, lane_key=task.key, cancel_event=stop, pause_event=pause)
            product = client.chat([{"content": task.key}])
            return SourceOutcome(task, translation=product, cancelled=stop.is_set())

    tasks = tuple(_task(tmp_path, key) for key in ("A", "B", "not-started"))
    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run_sources,
            tasks,
            Executor(),
            stop_event=stop,
            pause_event=pause,
            started=started.append,
            max_concurrent=1,
        )
        try:
            assert admitted.wait(2)
            _wait_until(lambda: budget.snapshot().waiting == 1)
            stop.set()
            budget.notify_state_changed()
            _wait_until(lambda: budget.snapshot().waiting == 0)
            assert not future.done()
            assert provider.calls == ["A"]
            assert budget.snapshot().in_flight == 1
        finally:
            release.set()
        outcomes = future.result(timeout=2)
    assert set(started) == {"A", "B"}
    assert [outcome.task.key for outcome in outcomes] == ["A", "B"]
    assert "cancelled" in outcomes[1].error
    assert outcomes[1].cancelled is True
    assert provider.calls == ["A"]
    assert budget.snapshot().in_flight == 0


@pytest.mark.parametrize("resume", [False, True])
def test_paused_source_dispatch_resumes_or_stops_without_starting_work(tmp_path, resume):
    stop = threading.Event()
    waiting = threading.Event()

    class ObservedPause(threading.Event):
        def wait(self, timeout=None):
            waiting.set()
            return super().wait(timeout)

    pause = ObservedPause()
    executed, started = [], []

    class Executor:
        def execute(self, task):
            executed.append(task.key)
            return SourceOutcome(task, translation="done")

    with ThreadPoolExecutor(max_workers=1) as caller:
        future = caller.submit(
            run_sources,
            (_task(tmp_path, "A"), _task(tmp_path, "B")),
            Executor(),
            stop_event=stop,
            pause_event=pause,
            started=started.append,
            max_concurrent=1,
        )
        try:
            assert waiting.wait(1)
            assert executed == started == []
            pause.set() if resume else stop.set()
            outcomes = future.result(timeout=2)
        finally:
            stop.set()
            pause.set()
    assert set(executed) == ({"A", "B"} if resume else set())
    assert [outcome.task.key for outcome in outcomes] == (["A", "B"] if resume else [])


def test_source_exception_preserves_other_outcomes_and_stable_source_order(tmp_path):
    stop, pause = _events()
    healthy_started = threading.Event()
    tasks = tuple(_task(tmp_path, key) for key in ("fails", "healthy", "next"))

    class Executor:
        def execute(self, task):
            if task.key == "fails":
                assert healthy_started.wait(2)
                raise ValueError("source provider failed")
            healthy_started.set()
            return SourceOutcome(task, translation=f"translation:{task.key}")

    outcomes = run_sources(
        tasks,
        Executor(),
        stop_event=stop,
        pause_event=pause,
        started=lambda _: None,
        max_concurrent=1,
    )
    assert [outcome.task.key for outcome in outcomes] == [task.key for task in tasks]
    assert outcomes[0].error == "source provider failed"
    assert outcomes[0].failed_keys == tuple(ai_entry_key(entry) for entry in tasks[0].entries)
    assert all(outcome.successful for outcome in outcomes[1:])
    assert [outcome.translation for outcome in outcomes[1:]] == ["translation:healthy", "translation:next"]


def test_same_named_plugins_use_stable_distinct_source_checkpoint_ids(tmp_path):
    first, second = _task(tmp_path, "project-source-a"), _task(tmp_path, "project-source-b")
    assert first.label == second.label
    assert Path(first.esp_path).name == Path(second.esp_path).name
    first_id = source_run_id("task-run", first.key)
    second_id = source_run_id("task-run", second.key)
    assert first_id != second_id
    assert source_run_id("task-run", first.key) == first_id
    assert source_run_id("retry-run", first.key) != first_id
    assert Path(first_id).name == first_id
    assert Path(second_id).name == second_id


def test_task_worker_factory_shares_one_executor_and_budget_across_live_sources(tmp_path):
    request = SimpleNamespace(request_budget=AiRequestBudget(2))
    tasks = (_task(tmp_path, "first"), _task(tmp_path, "second"))
    barrier = threading.Barrier(3)
    release = threading.Event()
    constructed, executed, completed = [], [], []

    def provider_call(_token):
        barrier.wait(timeout=3)
        assert release.wait(3)

    provider = _TrackingProvider(provider_call)

    class Executor:
        def __init__(self, actual_request, **kwargs):
            assert actual_request is request
            self.stop = kwargs["stop_event"]
            self.pause = kwargs["pause_event"]
            self.shared = kwargs["shared_terms"]
            self.lock = kwargs["terms_lock"]
            constructed.append(self)

        def execute(self, task):
            client = LimitedLLMClient(
                provider,
                request.request_budget,
                lane_key=task.key,
                cancel_event=self.stop,
                pause_event=self.pause,
            )
            product = client.chat([{"content": task.key}])
            with self.lock:
                self.shared[task.key] = product
                executed.append(self)
            return SourceOutcome(task, translation=product)

    worker = AiTaskWorker(request, tasks, executor_factory=Executor)
    worker.completed.connect(completed.append, Qt.ConnectionType.DirectConnection)
    worker.start()
    try:
        barrier.wait(timeout=3)
        assert request.request_budget.snapshot().in_flight == 2
    finally:
        release.set()
        assert worker.wait(3000)
    assert len(constructed) == 1
    assert executed == [constructed[0], constructed[0]]
    assert set(constructed[0].shared) == {"first", "second"}
    assert [outcome.task.key for outcome in completed[0]] == ["first", "second"]
    assert all(outcome.successful for outcome in completed[0])
    assert request.request_budget.snapshot().in_flight == 0
    assert provider.peak == 2
