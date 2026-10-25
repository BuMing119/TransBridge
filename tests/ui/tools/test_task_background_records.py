from dataclasses import replace
from threading import Event, get_ident
import time
from types import SimpleNamespace

import pytest

from tests.ui.tools import test_ai_task_session as support
from tests.ui.tools.test_task_recovery_binding import setup_binding
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_record_writer import TaskRecordWriter
from transbridge.ui.tools.ai_translator.task_recovery_binding import TaskRecoveryBinding

qapp = support.qapp


def wait_until(qapp, condition, timeout=5):
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()
    assert condition(), "background operation did not finish"


def async_binding(tmp_path):
    _, request, session, _ = setup_binding(tmp_path)
    request.run_id = "async"
    binding = TaskRecoveryBinding(request, session, asynchronous=True)
    entries = TaskEntryResults(session.tasks, request.run_id)
    run = SimpleNamespace(state="preparing", user_cancelled=False, shutting_down=False, entries=entries)
    return binding, request, session, run


def prepare(qapp, binding):
    ready, errors = [], []
    binding.prepare(on_success=ready.append, on_error=errors.append)
    wait_until(qapp, lambda: not binding.busy)
    assert len(ready) == 1 and not errors


def test_recovery_constructor_does_no_io_and_prepare_delivers_on_gui(qapp, tmp_path, monkeypatch):
    _, request, session, _ = setup_binding(tmp_path)
    request.run_id = "async"
    entered, release = Event(), Event()
    calls, delivered = [], []
    original = TaskRecoveryStore.create

    def create(store, record):
        calls.append(get_ident())
        entered.set()
        assert release.wait(5)
        return original(store, record)

    monkeypatch.setattr(TaskRecoveryStore, "create", create)
    binding = TaskRecoveryBinding(request, session, asynchronous=True)
    assert not calls and binding.record is None
    binding.prepare(on_success=lambda record: delivered.append((get_ident(), record)), on_error=pytest.fail)
    try:
        wait_until(qapp, entered.is_set)
        assert binding.busy and not delivered
    finally:
        release.set()
        wait_until(qapp, lambda: not binding.busy)
    assert calls == [calls[0]] and calls[0] != get_ident()
    assert delivered[0][0] == get_ident()
    assert delivered[0][1]["task_id"] == "async"


def test_recovery_failed_resume_validation_never_calls_ready(qapp, tmp_path):
    previous, request, session, _ = setup_binding(tmp_path)
    request.recovery_task_id = previous.record["task_id"]
    previous.release()
    request.config.model = "changed"
    binding = TaskRecoveryBinding(request, session, asynchronous=True)
    ready, errors, failures = [], [], []
    binding.failed.connect(failures.append)
    binding.prepare(on_success=ready.append, on_error=errors.append)
    wait_until(qapp, lambda: not binding.busy)
    assert not ready and len(errors) == len(failures) == 1
    assert "不匹配" in errors[0]
    assert binding.store.load(request.recovery_task_id) == previous.record


def test_recovery_latest_update_keeps_saved_evidence_and_detached_rows(qapp, tmp_path, monkeypatch):
    binding, _, session, run = async_binding(tmp_path)
    prepare(qapp, binding)
    entered, release = Event(), Event()
    states, threads = [], []
    original = binding.store.update

    def update(task_id, **fields):
        states.append(fields["state"])
        threads.append(get_ident())
        if len(states) == 1:
            entered.set()
            assert release.wait(5)
        return original(task_id, **fields)

    monkeypatch.setattr(binding.store, "update", update)
    run.state = "running"
    binding.update(run)
    try:
        wait_until(qapp, entered.is_set)
        key = next(iter(run.entries.entries))
        run.entries.entries[key] = replace(run.entries.entries[key], applied=True, text="已保存候选", stage=1)
        session.project_saved = True
        run.state = "partial"
        binding.update(run)
        # This latest update supersedes the saved generation before it starts.
        session.project_saved = False
        run.state = "running"
        binding.update(run)
        run.entries.entries[key] = replace(run.entries.entries[key], text="之后的改动")
        assert binding.busy
    finally:
        release.set()
        wait_until(qapp, lambda: not binding.busy)
    record = binding.store.load("async")
    assert states == ["running", "running"]
    assert all(thread != get_ident() for thread in threads)
    assert record["state"] == "running" and not record["project_saved"]
    assert record["applied_entries"][0]["text"] == "已保存候选"
    assert binding.record == record


def test_cancel_queued_during_recovery_initialization_is_durable(qapp, tmp_path, monkeypatch):
    binding, _, _, run = async_binding(tmp_path)
    entered, release = Event(), Event()
    original = binding.store.create

    def create(record):
        entered.set()
        assert release.wait(5)
        return original(record)

    monkeypatch.setattr(binding.store, "create", create)
    ready = []
    binding.prepare(on_success=ready.append, on_error=pytest.fail)
    try:
        wait_until(qapp, entered.is_set)
        run.user_cancelled = True
        run.state = "cancelling"
        binding.update(run)
        assert binding.busy and not ready
    finally:
        release.set()
        wait_until(qapp, lambda: not binding.busy)
    assert binding.store.load("async")["state"] == "cancelled"


def test_recovery_deferred_write_error_is_delivered(qapp, tmp_path, monkeypatch):
    binding, _, _, run = async_binding(tmp_path)
    prepare(qapp, binding)
    failures = []
    binding.failed.connect(failures.append)

    def failed(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(binding.store, "update", failed)
    run.state = "running"
    binding.update(run)
    wait_until(qapp, lambda: not binding.busy)
    assert len(failures) == 1 and "disk full" in failures[0]
    assert "disk full" in binding.error
    assert binding.record["state"] == "preparing"


def record_writer(tmp_path):
    _, request, session, _ = setup_binding(tmp_path)
    session.saved = session.snapshot_saved = False
    session.applied_keys = frozenset()
    session.history_dir = tmp_path / "history"
    session.version_label = "version"
    writer = TaskRecordWriter(request, session)
    ledger = TaskEntryResults(session.tasks, request.run_id)
    outcomes = tuple(SourceOutcome(task) for task in session.tasks)
    return writer, request, session, ledger, outcomes


def test_record_uses_frozen_ledger_without_copying_source_entries(qapp, tmp_path, monkeypatch):
    writer, _, _, ledger, outcomes = record_writer(tmp_path)
    entered, release = Event(), Event()
    original = TaskHistoryStore.save
    threads = []

    def save(store, record):
        threads.append(get_ident())
        entered.set()
        assert release.wait(5)
        return original(store, record)

    def reject_copy(*args):
        pytest.fail("ledger reporting must not copy mutable source entries")

    monkeypatch.setattr(writer, "_legacy_inputs", reject_copy)
    monkeypatch.setattr(TaskHistoryStore, "save", save)
    key = next(iter(ledger.entries))
    ledger.entries[key] = replace(ledger.entries[key], status="succeeded", text="冻结候选", stage=1)
    writer.write(outcomes, state="partial", entry_results=ledger)
    try:
        wait_until(qapp, entered.is_set)
        ledger.entries[key] = replace(ledger.entries[key], text="之后的改动")
        assert writer.busy
    finally:
        release.set()
        wait_until(qapp, lambda: not writer.busy)
    assert threads[0] != get_ident()
    rows = [row for source in writer.record["sources"] for row in source["snapshot"]["entries"]]
    assert next(row for row in rows if row["entry_key"] == key.to_dict())["candidate"] == "冻结候选"


def test_record_serial_coalescing_never_delivers_superseded_generation(qapp, tmp_path, monkeypatch):
    writer, _, session, ledger, outcomes = record_writer(tmp_path)
    entered, release = Event(), Event()
    states, ready = [], []
    original = TaskHistoryStore.save

    def save(store, record):
        states.append(record["state"])
        if len(states) == 1:
            entered.set()
            assert release.wait(5)
        return original(store, record)

    monkeypatch.setattr(TaskHistoryStore, "save", save)
    writer.ready.connect(lambda record: ready.append(record["state"]))
    writer.write(outcomes, state="running", entry_results=ledger)
    try:
        wait_until(qapp, entered.is_set)
        writer.write(outcomes, state="partial", entry_results=ledger)
        session.saved = session.project_saved = session.snapshot_saved = True
        writer.write(outcomes, state="completed", entry_results=ledger)
    finally:
        release.set()
        wait_until(qapp, lambda: not writer.busy)
    assert states == ["running", "completed"]
    assert ready == ["completed"]
    assert writer.record["saved"] and writer.record["project_saved"]
