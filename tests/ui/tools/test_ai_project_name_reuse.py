"""Known translations reach task drafts before the first unknown-name model call."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from tests.ui.tools import test_ai_project_name_preparation as support
from transbridge.application.translation import FilesystemTranslationCheckpointPort
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker

name_run = support.name_run


@pytest.mark.parametrize("name_run", [False, True], indirect=True)
def test_known_names_are_committed_while_unknown_name_model_is_still_blocked(name_run, monkeypatch):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    first = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    second = run.task("B", [("known", "Whiterun", "LCTN:FULL")])
    entered, release = Event(), Event()
    original_chat = run.provider.chat
    written = {}

    def chat(*args, **kwargs):
        for task in (first, second):
            entry = task.entries[0]
            snapshot = task.collection.get(entry.identity).snapshot()
            assert snapshot.translation == "已有雪漫" and snapshot.stage == 2
            assert snapshot.revision == entry.revision.next()
            written[task.key] = snapshot
        entered.set()
        assert release.wait(5)
        return original_chat(*args, **kwargs)

    monkeypatch.setattr(run.provider, "chat", chat)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run.executor.prepare_names, (first, second))
        try:
            assert entered.wait(5)
            assert not future.done()
            assert first.collection.get(first.entries[1].identity).translation == ""
        finally:
            release.set()
        future.result(timeout=5)
    assert run.provider.counts() == {"Iron Sword": 1}
    outcomes = tuple(run.executor.execute(task) for task in (first, second))
    outcomes = run.executor.close_terms(outcomes)
    assert all(outcome.successful for outcome in outcomes), [outcome.error for outcome in outcomes]
    assert sum(outcome.translation.success_count for outcome in outcomes) == 3
    assert run.provider.counts() == {"Iron Sword": 1}
    if not run.request.config.enable_post_process:
        for task in (first, second):
            assert task.collection.get(task.entries[0].identity).snapshot() == written[task.key]
    for outcome in outcomes:
        assert all(item.status == "succeeded" for item in outcome.translation.entry_outcomes.values())


def test_failed_early_candidate_storage_prevents_model_calls_and_draft_writes(name_run, monkeypatch):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    before = tuple(entry.snapshot() for entry in task.collection)

    def fail(*_args):
        raise OSError("early reuse storage unavailable")

    monkeypatch.setattr(FilesystemTranslationCheckpointPort, "save", fail)
    with pytest.raises(OSError, match="early reuse storage unavailable"):
        run.executor.prepare_names((task,))
    assert tuple(entry.snapshot() for entry in task.collection) == before
    assert run.provider.calls == []


def test_pause_before_draft_commit_waits_and_cancellation_prevents_late_write(name_run, monkeypatch):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    saved = Event()
    original_save = FilesystemTranslationCheckpointPort.save

    def save(port, checkpoint):
        original_save(port, checkpoint)
        if checkpoint.run_id.endswith("-name-reuse") and not checkpoint.committed_candidate_ids:
            run.executor.pause.clear()
            saved.set()

    monkeypatch.setattr(FilesystemTranslationCheckpointPort, "save", save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run.executor.prepare_names, (task,))
        try:
            assert saved.wait(5)
            assert not future.done()
            assert all(entry.translation == "" for entry in task.collection)
        finally:
            run.executor.stop.set()
            run.executor.pause.set()
        with pytest.raises(RuntimeError, match="未能完整提交"):
            future.result(timeout=5)
    assert all(entry.translation == "" for entry in task.collection)
    assert run.provider.calls == []


def test_model_failure_keeps_early_commit_and_later_reports_only_failed_unknown_name(name_run):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    run.provider.fail.add("Iron Sword")
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    run.executor.prepare_names((task,))
    snapshot = task.collection.get(task.entries[0].identity).snapshot()
    outcome = run.executor.execute(task)
    assert outcome.translation.success_count == 1 and outcome.translation.failed_count == 1
    assert outcome.failed_keys == ("new",)
    assert task.collection.get(task.entries[0].identity).snapshot() == snapshot
    assert run.provider.counts().get("Whiterun", 0) == 0


def test_cancelled_name_barrier_delivers_written_text_without_claiming_full_success(name_run, monkeypatch):
    from PyQt6.QtCore import Qt

    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    outcomes = []
    worker = AiTaskWorker(run.request, (task,))
    worker.completed.connect(outcomes.append, Qt.ConnectionType.DirectConnection)
    original_chat = run.provider.chat

    def chat(*args, **kwargs):
        assert task.collection.get(task.entries[0].identity).translation == "已有雪漫"
        worker.stop()
        return original_chat(*args, **kwargs)

    monkeypatch.setattr(run.provider, "chat", chat)
    worker.run()
    assert worker.was_cancelled and len(outcomes) == 1
    result = outcomes[0][0].translation.entry_outcomes[task.entries[0].identity]
    assert result.text == "已有雪漫" and result.status == "cancelled"
    ledger = TaskEntryResults((task,), run.request.run_id)
    ledger.ingest(outcomes[0], set(ledger.entries), cancelled=True)
    assert not ledger.ready_keys
    assert ledger.entries[task.entries[0].identity].text == "已有雪漫"


def test_later_source_storage_failure_preserves_prior_draft_without_starting_models(name_run, monkeypatch):
    from PyQt6.QtCore import Qt

    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    first = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("new", "Iron Sword", "WEAP:FULL")])
    second = run.task("B", [("known", "Whiterun", "LCTN:FULL")])
    original_save = FilesystemTranslationCheckpointPort.save
    sources = set()

    def save(port, checkpoint):
        if checkpoint.run_id.endswith("-name-reuse"):
            sources.add(checkpoint.run_id)
            if len(sources) > 1:
                raise OSError("second source storage unavailable")
        original_save(port, checkpoint)

    monkeypatch.setattr(FilesystemTranslationCheckpointPort, "save", save)
    outcomes = []
    worker = AiTaskWorker(run.request, (first, second))
    worker.completed.connect(outcomes.append, Qt.ConnectionType.DirectConnection)
    worker.run()
    assert first.collection.get(first.entries[0].identity).translation == "已有雪漫"
    assert second.collection.get(second.entries[0].identity).translation == ""
    assert run.provider.calls == []
    result = outcomes[0][0].translation.entry_outcomes[first.entries[0].identity]
    assert result.text == "已有雪漫" and result.status == "failed"
    ledger = TaskEntryResults((first, second), run.request.run_id)
    ledger.ingest(outcomes[0], set(ledger.entries))
    assert not ledger.ready_keys


def test_changed_early_draft_is_rejected_before_later_source_model_work(name_run):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL"), ("body", "Continue.", "BOOK:DESC")])
    run.executor.prepare_names((task,))
    changed = replace(task.collection.get(task.entries[0].identity), translation="其他译文")
    task = replace(task, collection=type(task.collection)([changed, task.entries[1]]))
    outcome = run.executor.execute(task)
    assert "草稿已变化" in outcome.error
    assert run.provider.calls == []


def test_repeating_preparation_does_not_write_same_draft_twice(name_run):
    run = name_run
    run.executor.shared_term_pool.publish([("Whiterun", "已有雪漫", "test", "")])
    task = run.task("A", [("known", "Whiterun", "LCTN:FULL")])
    run.executor.prepare_names((task,))
    snapshot = task.collection.get(task.entries[0].identity).snapshot()
    run.executor.prepare_names((task,))
    assert task.collection.get(task.entries[0].identity).snapshot() == snapshot
    assert run.provider.calls == []
