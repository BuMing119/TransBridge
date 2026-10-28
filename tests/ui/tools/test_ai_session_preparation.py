from __future__ import annotations

from dataclasses import replace
from threading import Event, get_ident
import time
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools import test_ai_task_session as support
from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.tools.ai_translator.task_session_preparation import SessionCapture
from transbridge.ui.version_persistence import VersionPersistence

session = support.session
_application = None


@pytest.fixture(scope="module")
def qapp():
    global _application
    _application = QApplication.instance() or QApplication([])
    return _application


def _inputs(monkeypatch, count=1):
    monkeypatch.setattr(task_session, "VersionPersistence", support._Persistence)
    ctx = support._Context()
    template = support._entry("first")
    entries = tuple(
        replace(template, id=f"e{i}", key=f"e{i}", entry_key=EntryKey(SourceNamespace("first"), f"e{i}"))
        for i in range(count)
    )
    collection = TranslationEntryCollection(entries)
    ctx.slots["first"].collection = collection
    tasks = (SourceTask("first", "First", None, collection, entries, ()),)
    return ctx, tasks, SimpleNamespace(mode="translate", run_id="prepared-run")


def _wait(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert predicate()


def test_prepare_runs_off_gui_and_delivers_detached_initial_results(qapp, monkeypatch):
    ctx, tasks, spec = _inputs(monkeypatch)
    gui = get_ident()
    threads, results, errors, callbacks = [], [], [], []
    original = SessionCapture.prepare

    def prepare(capture):
        threads.append(get_ident())
        return original(capture)

    def ready(session):
        callbacks.append(get_ident())
        results.append(session)

    monkeypatch.setattr(SessionCapture, "prepare", prepare)
    job = task_session.TaskSession.create_async(ctx, tasks, spec, on_success=ready, on_error=errors.append)
    assert job.is_busy and not results
    _wait(qapp, lambda: not job.is_busy)
    assert threads and threads != [gui] and callbacks == [gui] and not errors
    session = results[0]
    entry = session.tasks[0].entries[0]
    assert session.initial_entry_results.run_id == spec.run_id
    assert session.initial_entry_results._index is not None
    entry.metadata[0][1]["value"].append(99)
    assert tasks[0].entries[0].metadata[0][1]["value"] == [1]
    assert session.initial_entry_results.entries[entry.identity].before.metadata[0][1]["value"] == [1]


@pytest.mark.parametrize("change", ["entry", "nested_metadata", "revision", "source", "identity"])
def test_mutation_after_capture_rejects_prepared_session(qapp, monkeypatch, change):
    ctx, tasks, spec = _inputs(monkeypatch)
    entered, release = Event(), Event()
    original = SessionCapture.prepare

    def prepare(capture):
        entered.set()
        assert release.wait(5)
        return original(capture)

    monkeypatch.setattr(SessionCapture, "prepare", prepare)
    results, errors = [], []
    job = task_session.TaskSession.create_async(ctx, tasks, spec, on_success=results.append, on_error=errors.append)
    _wait(qapp, entered.is_set)
    if change == "entry":
        tasks[0].entries[0].translation = "用户修改"
    elif change == "nested_metadata":
        tasks[0].entries[0].metadata[0][1]["value"].append(2)
    elif change == "revision":
        ctx.variant_revision += 1
    elif change == "source":
        ctx.slots.pop("third")
    else:
        ctx.active_version_identity = ("project", "other")
    release.set()
    _wait(qapp, lambda: not job.is_busy)
    assert not results and len(errors) == 1


def test_capture_yields_and_rechecks_previously_captured_entries(qapp, monkeypatch):
    ctx, tasks, spec = _inputs(monkeypatch, count=400)
    ticks, results, errors = [], [], []
    job = task_session.TaskSession.create_async(ctx, tasks, spec, on_success=results.append, on_error=errors.append)

    def edit_first_slice():
        ticks.append(True)
        tasks[0].entries[0].translation = "片间修改"

    QTimer.singleShot(0, edit_first_slice)
    _wait(qapp, lambda: not job.is_busy)
    assert ticks and not results and errors


@pytest.mark.parametrize("phase", ["capture", "worker"])
def test_cancel_retains_worker_and_never_delivers_session(qapp, monkeypatch, phase):
    ctx, tasks, spec = _inputs(monkeypatch)
    entered, release = Event(), Event()
    original = SessionCapture.prepare

    def prepare(capture):
        entered.set()
        assert release.wait(5)
        return original(capture)

    monkeypatch.setattr(SessionCapture, "prepare", prepare)
    results, errors = [], []
    job = task_session.TaskSession.create_async(ctx, tasks, spec, on_success=results.append, on_error=errors.append)
    if phase == "worker":
        _wait(qapp, entered.is_set)
    job.cancel()
    if phase == "worker":
        qapp.processEvents()
        assert job.is_busy
    release.set()
    _wait(qapp, lambda: not job.is_busy)
    assert not results and len(errors) == 1 and "取消" in errors[0]


def test_retry_uses_fixed_inputs_and_keeps_concurrent_workbench_edit(qapp, session, monkeypatch):
    support._capture(qapp, session)
    before = session.tasks
    key = before[0].entries[0].identity
    before[0].entries[0].translation = "失败草稿"
    results, errors = [], []
    session.retry_entries_async({key}, on_success=results.append, on_error=errors.append)
    assert session.tasks is before
    next(iter(session._ctx.collection)).translation = "用户修改"
    support._wait(qapp, session)
    assert results and not errors and session.tasks is not before
    assert results[0][0].entries[0].translation == ""
    assert next(iter(session._ctx.collection)).translation == "用户修改"
    assert before[0].entries[0].translation == "失败草稿"


def test_background_apply_validation_never_commits_failed_input(qapp, session):
    support._capture(qapp, session)
    gui, threads = get_ident(), []
    results, errors = [], []

    def validate():
        threads.append(get_ident())
        raise RuntimeError("术语文件已变化")

    session.apply_entries_async(
        {session.tasks[0].entries[0].identity},
        on_success=results.append,
        on_error=errors.append,
        background_validate=validate,
    )
    support._wait(qapp, session)
    assert threads and threads != [gui] and not results and errors
    assert not session.applied_keys and not session._persistence.commits


def test_snapshot_and_save_workers_use_frozen_ports_without_reading_gui_context(qapp, monkeypatch):
    gui, calls = get_ident(), []

    class GuiContext(support._Context):
        def __getattribute__(self, name):
            assert get_ident() == gui, f"background read of GUI context: {name}"
            return super().__getattribute__(name)

    ctx = GuiContext()
    ctx.runtime_context = RequestContext(owner_id="test")

    def snapshot(name, context):
        assert get_ident() != gui
        assert (context.project_id, context.variant_id) == ("project", "variant")
        calls.append(("snapshot", name))
        return OperationResult.completed({})

    def save(context):
        assert get_ident() != gui
        calls.append(("save", context))
        return OperationResult.completed({})

    from transbridge.application.translation.result_merge import merge_result_entries

    current = {entry.identity: support._state(entry) for slot in ctx.slots.values() for entry in slot.collection}

    def commit(candidates, context, *, policy, before_commit=None, **options):
        assert get_ident() != gui
        plan = merge_result_entries(
            {row.base.entry_key: row.base for row in candidates},
            current,
            {row.incoming.entry_key: row.incoming for row in candidates},
            policy=policy,
        )
        if before_commit is not None:
            before_commit(plan)
        calls.append(("merge", context))
        return OperationResult.completed(plan)

    ctx.project_commands = SimpleNamespace(save_snapshot=snapshot, save=save, merge_entry_states=commit)
    slot = ctx.slots["first"]
    tasks = (SourceTask("first", "First", None, slot.collection, tuple(slot.collection), ()),)
    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    session = task_session.TaskSession(ctx, tasks, SimpleNamespace(run_id="ports"))
    assert support._capture(qapp, session)[0]
    applied, errors = [], []
    session.apply_entries_async(
        {session.tasks[0].entries[0].identity}, on_success=applied.append, on_error=errors.append
    )
    support._wait(qapp, session)
    assert applied and not errors
    success, errors = support._save(qapp, session)
    assert success and not errors and [kind for kind, _ in calls] == ["merge", "save"]


@pytest.mark.parametrize("edit_after_commit", [False, True])
@pytest.mark.parametrize("reproject", [False, True])
def test_partial_merge_never_recaptures_full_project_or_absorbs_later_edit(
    qapp, session, monkeypatch, edit_after_commit, reproject
):
    support._capture(qapp, session)
    captures, success, errors = [], [], []

    def steps(capture):
        captures.append(capture.run_id)
        raise AssertionError("partial merge must not recapture the whole project")
        yield

    original_merge = session._persistence.merge_translation

    def merge(*args, **kwargs):
        result = original_merge(*args, **kwargs)
        if reproject:
            for slot in session._ctx.slots.values():
                slot.collection = TranslationEntryCollection(
                    replace(entry, revision=entry.revision.next()) for entry in slot.collection
                )
            session._ctx.variant_revision += 1
        if edit_after_commit:
            next(iter(session._ctx.collection)).translation = "提交之后的新编辑"
        return result

    monkeypatch.setattr(SessionCapture, "steps", steps)
    monkeypatch.setattr(session._persistence, "merge_translation", merge)
    key = session.tasks[0].entries[0].identity
    session.tasks[0].entries[0].translation = "已应用候选"
    session.apply_entries_async({key}, on_success=success.append, on_error=errors.append)
    support._wait(qapp, session)
    assert success and not errors and session.applied_keys == frozenset({key})
    assert not captures
    session.require_current()
    if edit_after_commit:
        assert next(iter(session._ctx.collection)).translation == "提交之后的新编辑"
        session._ctx.dirty = False
        assert not session.observe_project_saved()
    else:
        assert next(iter(session._ctx.collection)).translation == "已应用候选"
