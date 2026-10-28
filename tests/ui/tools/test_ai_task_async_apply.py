from __future__ import annotations

from dataclasses import replace
from threading import Event, get_ident
import time
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools import test_ai_task_session as support
from tests.ui.tools.test_ai_task_session import _capture, _wait
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.version_persistence import VersionPersistence

base_session = support.session
_application = None


@pytest.fixture
def session(base_session):
    ctx = base_session._ctx
    ctx.runtime_context = object()
    base_session.port_calls = []
    ctx.project_commands = support._v2_commands(ctx, base_session.port_calls)
    base_session._persistence = VersionPersistence(ctx, ctx.active_version_identity)
    return base_session


@pytest.fixture(scope="module")
def qapp():
    # Retain the application across modules that share the global worker buses.
    global _application
    _application = QApplication.instance() or QApplication([])
    return _application


def _start(session, **kwargs):
    entry = session.tasks[0].entries[0]
    entry.translation = "后台准备的新译文"
    success, errors = [], []
    session.apply_entries_async(
        {entry.identity}, on_success=kwargs.pop("on_success", success.append), on_error=errors.append, **kwargs
    )
    return success, errors


def test_merge_and_evidence_io_are_background_callbacks_are_gui_and_can_save(qapp, session, monkeypatch, tmp_path):
    _capture(qapp, session)
    gui_thread = get_ident()
    prepare_threads, commit_threads, callback_threads = [], [], []
    original_commit = session._ctx.project_commands.merge_entry_states

    def commit(*args, **kwargs):
        prepare_threads.append(get_ident())
        return original_commit(*args, **kwargs)

    saved, save_errors = [], []

    def applied(result):
        callback_threads.append(get_ident())
        assert not session.is_busy
        session.save_translation(on_success=saved.append, on_error=save_errors.append)

    session.merge_evidence_writer = support._Evidence(tmp_path, lambda *args: commit_threads.append(get_ident()))
    monkeypatch.setattr(session._ctx.project_commands, "merge_entry_states", commit)
    _, errors = _start(session, on_success=applied)
    _wait(qapp, session)
    assert prepare_threads and prepare_threads != [gui_thread]
    assert commit_threads and all(thread != gui_thread for thread in commit_threads)
    assert callback_threads == [gui_thread]
    assert saved and not errors and not save_errors
    assert next(iter(session._ctx.collection)).translation == "后台准备的新译文"
    assert len(session.merge_evidence_writer.receipts) == 1
    assert session.merge_evidence_writer.load()[0]["applied_keys"] == [session.tasks[0].entries[0].identity.to_dict()]


@pytest.mark.parametrize("change", ["revision", "entry"])
def test_changes_during_merge_use_actual_per_entry_result(qapp, session, tmp_path, change):
    _capture(qapp, session)
    entered, release = Event(), Event()

    def preparing():
        entered.set()
        assert release.wait(5)

    session._ctx.project_commands = support._v2_commands(session._ctx, session.port_calls, before_merge=preparing)
    session.merge_evidence_writer = support._Evidence(tmp_path)
    success, errors = _start(session)
    assert entered.wait(5)
    if change == "revision":
        session._ctx.variant_revision += 1
    elif change == "entry":
        next(iter(session._ctx.collection)).translation = "用户编辑"
    release.set()
    _wait(qapp, session)
    assert success and not errors
    key = session.tasks[0].entries[0].identity
    if change == "entry":
        assert success[0].kept_current_keys == (key,) and not session.applied_keys
        assert next(iter(session._ctx.collection)).translation == "用户编辑"
        assert session.merge_evidence_writer.load()[0]["applied_keys"] == []
    else:
        assert session.applied_keys == frozenset({key})


def test_gui_timer_runs_while_preparing_and_busy_blocks_second_apply(qapp, session, monkeypatch):
    _capture(qapp, session)
    released = Event()
    original = session._ctx.project_commands.merge_entry_states
    ticks = []

    def prepare(*args, **kwargs):
        assert released.wait(5), "GUI timer did not run during detached preparation"
        return original(*args, **kwargs)

    def tick():
        ticks.append(True)
        released.set()

    monkeypatch.setattr(session._ctx.project_commands, "merge_entry_states", prepare)
    success, errors = _start(session)
    assert session.is_busy
    with pytest.raises(RuntimeError, match="正在进行"):
        _start(session)
    QTimer.singleShot(0, tick)
    _wait(qapp, session)
    assert ticks and success and not errors


def test_validation_runs_before_commit_and_failure_releases_busy(qapp, session):
    _capture(qapp, session)
    calls = []

    def validate():
        calls.append(get_ident())
        raise RuntimeError("术语已修改")

    with pytest.raises(RuntimeError, match="术语已修改"):
        _start(session, validate=validate)
    assert calls == [get_ident()]
    assert not session.is_busy
    assert not any(kind == "merge" for kind, _ in session.port_calls)
    assert next(iter(session._ctx.collection)).translation == ""


def test_prepare_failure_keeps_live_state_and_releases_busy(qapp, session, monkeypatch):
    _capture(qapp, session)

    def prepare(*args, **kwargs):
        raise ValueError("无法构造应用结果")

    monkeypatch.setattr(session._ctx.project_commands, "merge_entry_states", prepare)
    success, errors = _start(session)
    _wait(qapp, session)
    assert errors == ["无法构造应用结果"] and not success
    assert not any(kind == "merge" for kind, _ in session.port_calls)
    assert next(iter(session._ctx.collection)).translation == ""


def test_commit_failure_never_publishes_or_marks_results_applied(qapp, session, monkeypatch, tmp_path):
    _capture(qapp, session)
    session.merge_evidence_writer = support._Evidence(tmp_path)

    def failed(*args, **kwargs):
        raise OSError("项目写入失败")

    monkeypatch.setattr(session._ctx.project_commands, "merge_entry_states", failed)
    success, errors = _start(session)
    _wait(qapp, session)
    assert errors and not success
    assert not session.applied_keys
    assert not session._ctx.notifications
    assert next(iter(session._ctx.collection)).translation == ""
    assert session.merge_evidence_writer.receipts == []
    assert session.merge_evidence_writer.load() == ()


def test_draft_values_are_captured_before_async_dispatch(qapp, session):
    _capture(qapp, session)
    success, errors = _start(session)
    session.tasks[0].entries[0].translation = "之后修改的草稿"
    _wait(qapp, session)
    assert success and not errors
    assert next(iter(session._ctx.collection)).translation == "后台准备的新译文"


@pytest.mark.parametrize("field", ["translation", "nested_metadata"])
def test_fixed_baseline_remains_detached_after_publication_without_global_gate(qapp, session, field):
    _capture(qapp, session)
    success, errors = _start(session)
    _wait(qapp, session)
    assert success and not errors
    entry = next(iter(session._ctx.collection))
    if field == "translation":
        entry.translation = "后续用户修改"
    else:
        entry.metadata[0][1]["value"].append(9)
    session.require_current()
    before = session._draft.originals[entry.identity]
    assert before.translation == "" and before.metadata[0][1]["value"] == [1]


@pytest.mark.slow
def test_large_async_session_reports_preparation_and_gui_latency(qapp, monkeypatch):
    """Synthetic diagnostic: keep timing visible without machine-specific pass thresholds."""
    timings = {}

    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    entries = tuple(
        replace(support._entry("bench"), key=str(index), entry_key=EntryKey(SourceNamespace("bench"), str(index)))
        for index in range(8300)
    )
    collection = TranslationEntryCollection(entries)
    context = support._Context()
    context.slots = {"first": CollectionSlot("synthetic", collection)}
    context.runtime_context = object()
    calls = []
    context.project_commands = support._v2_commands(context, calls)
    task = SourceTask("first", "synthetic", None, collection, entries, ())
    current = task_session.TaskSession(context, (task,), SimpleNamespace(mode="translate", run_id="benchmark"))
    current._captured = True
    for entry in current.tasks[0].entries:
        entry.translation = "合成测试译文"
    original_prepare = context.project_commands.merge_entry_states

    def timed_prepare(*args, **kwargs):
        started = time.perf_counter()
        result = original_prepare(*args, **kwargs)
        timings["prepare_seconds"] = time.perf_counter() - started
        return result

    monkeypatch.setattr(context.project_commands, "merge_entry_states", timed_prepare)
    ticks, failures, saved = [time.perf_counter()], [], []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()

    def applied(_):
        started = time.perf_counter()
        current.save_translation(on_success=saved.append, on_error=failures.append)
        timings["save_dispatch_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    current.apply_entries_async({entry.identity for entry in entries}, on_success=applied, on_error=failures.append)
    timings["apply_dispatch_seconds"] = time.perf_counter() - started
    _wait(qapp, current)
    ticks.append(time.perf_counter())
    timer.stop()
    timings["total_seconds"] = time.perf_counter() - started
    timings["max_heartbeat_gap_seconds"] = max(b - a for a, b in zip(ticks, ticks[1:]))
    timings["heartbeat_count"] = len(ticks) - 2
    print("SYNTHETIC_8300_ASYNC", timings)
    assert saved and not failures
    assert len(current.applied_keys) == 8300
    assert len(ticks) > 2
    assert [kind for kind, _ in calls] == ["merge", "save"]


def test_background_validation_runs_once_before_authoritative_commit(qapp, session):
    _capture(qapp, session)
    threads = []
    success, errors = _start(session, background_validate=lambda: threads.append(get_ident()))
    _wait(qapp, session)
    assert success and not errors
    assert len(threads) == 1 and threads[0] != get_ident()


@pytest.mark.parametrize("change", ["identity", "cancel"])
def test_completed_authoritative_commit_always_delivers_actual_receipt(qapp, session, monkeypatch, change):
    _capture(qapp, session)
    committed, release = Event(), Event()
    merge = session._ctx.project_commands.merge_entry_states

    def delayed_delivery(*args, **kwargs):
        result = merge(*args, **kwargs)
        committed.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(session._ctx.project_commands, "merge_entry_states", delayed_delivery)
    success, errors = _start(session)
    assert committed.wait(5)
    if change == "identity":
        session._ctx.active_version_identity = ("project", "other")
    else:
        session.rollback_uncommitted()
    release.set()
    _wait(qapp, session)
    key = session.tasks[0].entries[0].identity
    assert success and not errors
    assert session.applied_keys == frozenset({key})
    assert success[0].applied[0].translation == "后台准备的新译文"
    if change == "identity":
        assert not session._ctx.notifications


def test_unchanged_receipt_uses_frozen_candidate_after_later_draft_edit(qapp, session):
    _capture(qapp, session)
    candidate = session.tasks[0].entries[0]
    success, errors = [], []
    session.apply_entries_async({candidate.identity}, on_success=success.append, on_error=errors.append)
    candidate.translation = "dispatch之后改变草稿"
    _wait(qapp, session)
    assert success and not errors and success[0].unchanged_keys == (candidate.identity,)
    assert next(iter(session._ctx.collection)).translation == ""
    session._ctx.dirty = False
    assert session.observe_project_saved()
    assert session.project_saved
