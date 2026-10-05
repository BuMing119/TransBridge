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
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.tools.ai_translator.task_apply_preparation import DraftMergeInput
from transbridge.ui.tools.ai_translator.task_scope import SourceTask

session = support.session
_application = None


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


def test_prepare_is_background_but_commit_and_callbacks_are_gui_and_can_save(qapp, session, monkeypatch):
    _capture(qapp, session)
    gui_thread = get_ident()
    prepare_threads, commit_threads, callback_threads = [], [], []
    original_prepare = DraftMergeInput.prepare
    original_commit = session._persistence.commit_translation

    def prepare(inputs):
        prepare_threads.append(get_ident())
        return original_prepare(inputs)

    def commit(entries):
        commit_threads.append(get_ident())
        return original_commit(entries)

    saved, save_errors = [], []

    def applied(result):
        callback_threads.append(get_ident())
        assert not session.is_busy
        session.save_translation(on_success=saved.append, on_error=save_errors.append)

    monkeypatch.setattr(DraftMergeInput, "prepare", prepare)
    monkeypatch.setattr(session._persistence, "commit_translation", commit)
    _, errors = _start(session, on_success=applied)
    _wait(qapp, session)
    assert prepare_threads and prepare_threads != [gui_thread]
    assert commit_threads == callback_threads == [gui_thread]
    assert saved and not errors and not save_errors
    assert next(iter(session._ctx.collection)).translation == "后台准备的新译文"


@pytest.mark.parametrize("change", ["revision", "identity", "entry", "cancel"])
def test_changes_during_prepare_never_commit(qapp, session, change):
    _capture(qapp, session)
    success, errors = _start(session)
    if change == "revision":
        session._ctx.variant_revision += 1
    elif change == "identity":
        session._ctx.active_version_identity = ("project", "another")
    elif change == "entry":
        next(iter(session._ctx.collection)).translation = "用户编辑"
    else:
        session.rollback_uncommitted()
    _wait(qapp, session)
    assert errors and not success
    assert not session._persistence.commits
    assert not session.applied_keys


def test_gui_timer_runs_while_preparing_and_busy_blocks_second_apply(qapp, session, monkeypatch):
    _capture(qapp, session)
    released = Event()
    original = DraftMergeInput.prepare
    ticks = []

    def prepare(inputs):
        assert released.wait(5), "GUI timer did not run during detached preparation"
        return original(inputs)

    def tick():
        ticks.append(True)
        released.set()

    monkeypatch.setattr(DraftMergeInput, "prepare", prepare)
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

    success, errors = _start(session, validate=validate)
    _wait(qapp, session)
    assert calls == [get_ident()]
    assert errors == ["术语已修改"] and not success
    assert not session._persistence.commits


def test_prepare_failure_keeps_live_state_and_releases_busy(qapp, session, monkeypatch):
    _capture(qapp, session)

    def prepare(inputs):
        raise ValueError("无法构造应用结果")

    monkeypatch.setattr(DraftMergeInput, "prepare", prepare)
    success, errors = _start(session)
    _wait(qapp, session)
    assert errors == ["无法构造应用结果"] and not success
    assert not session._persistence.commits
    assert next(iter(session._ctx.collection)).translation == ""


def test_commit_failure_never_publishes_or_marks_results_applied(qapp, session):
    _capture(qapp, session)
    session._persistence.fail_commit = True
    success, errors = _start(session)
    _wait(qapp, session)
    assert errors and not success
    assert not session.applied_keys
    assert not session._ctx.notifications
    assert next(iter(session._ctx.collection)).translation == ""


def test_draft_values_are_captured_before_async_dispatch(qapp, session):
    _capture(qapp, session)
    success, errors = _start(session)
    session.tasks[0].entries[0].translation = "之后修改的草稿"
    _wait(qapp, session)
    assert success and not errors
    assert next(iter(session._ctx.collection)).translation == "后台准备的新译文"


@pytest.mark.parametrize("field", ["translation", "nested_metadata"])
def test_prepared_baseline_remains_detached_after_publication(qapp, session, field):
    _capture(qapp, session)
    success, errors = _start(session)
    _wait(qapp, session)
    assert success and not errors
    entry = next(iter(session._ctx.collection))
    if field == "translation":
        entry.translation = "后续用户修改"
    else:
        entry.metadata[0][1]["value"].append(9)
    with pytest.raises(RuntimeError, match="已修改"):
        session.require_current()


@pytest.mark.slow
def test_large_async_session_reports_preparation_and_gui_latency(qapp, monkeypatch):
    """Synthetic diagnostic: keep timing visible without machine-specific pass thresholds."""
    timings = {}

    class MemoryPersistence:
        project_saved = snapshot_saved = False

        def __init__(self, *args):
            pass

        def commit_translation(self, entries):
            return SimpleNamespace(is_success=True)

        def save_translation(self, entries, name):
            self.project_saved = self.snapshot_saved = True
            return SimpleNamespace(is_success=True)

    monkeypatch.setattr(task_session, "VersionPersistence", MemoryPersistence)
    entries = tuple(
        replace(support._entry("bench"), key=str(index), entry_key=EntryKey(SourceNamespace("bench"), str(index)))
        for index in range(8300)
    )
    collection = TranslationEntryCollection(entries)
    context = support._Context()
    context.slots = {"first": CollectionSlot("synthetic", collection)}
    task = SourceTask("first", "synthetic", None, collection, entries, ())
    current = task_session.TaskSession(context, (task,), SimpleNamespace(mode="translate", run_id="benchmark"))
    current._captured = True
    for entry in current.tasks[0].entries:
        entry.translation = "合成测试译文"
    original_prepare = DraftMergeInput.prepare

    def timed_prepare(inputs):
        started = time.perf_counter()
        result = original_prepare(inputs)
        timings["prepare_seconds"] = time.perf_counter() - started
        return result

    monkeypatch.setattr(DraftMergeInput, "prepare", timed_prepare)
    for name in ("_commit_entries", "_read_states"):
        original = getattr(current, name)

        def measured(*args, _original=original, _name=name, **kwargs):
            started = time.perf_counter()
            result = _original(*args, **kwargs)
            timings[_name + "_seconds"] = time.perf_counter() - started
            return result

        monkeypatch.setattr(current, name, measured)
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
