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
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_session
from transbridge.ui.tools.ai_translator.task_scope import SourceTask
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


def test_retry_is_detached_until_gui_validation(qapp, session, monkeypatch):
    support._capture(qapp, session)
    before = session.tasks
    key = before[0].entries[0].identity
    before[0].entries[0].translation = "失败草稿"
    results, errors = [], []
    session.retry_entries_async({key}, on_success=results.append, on_error=errors.append)
    assert session.tasks is before
    next(iter(session._ctx.collection)).translation = "用户修改"
    support._wait(qapp, session)
    assert not results and errors and session.tasks is before
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


def test_legacy_background_snapshot_does_not_mutate_live_store(qapp, monkeypatch, tmp_path):
    from transbridge.persistence.variant_store import VariantStore

    ctx, tasks, spec = _inputs(monkeypatch)
    ctx.uses_authoritative_projection = False
    ctx.slots = {"first": ctx.slots["first"]}
    ctx.active_project = SimpleNamespace(variant_dir=lambda _name: tmp_path)
    ctx.active_variant = "main"
    ctx.variant_store = VariantStore(tmp_path / "current.json")
    ctx.variant_store.translations = {"existing": "应保留"}
    ctx.variant_store.dirty = True
    ctx.entry_labels, ctx.label_library = {}, {}
    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    results, errors = [], []
    job = task_session.TaskSession.create_async(ctx, tasks, spec, on_success=results.append, on_error=errors.append)
    _wait(qapp, lambda: not job.is_busy)
    assert results and not errors
    success, errors = support._capture(qapp, results[0])
    assert success and not errors and ctx.variant_store.translations == {"existing": "应保留"}
    assert ctx.variant_store.dirty
    snapshot = VariantStore.load(next((tmp_path / "snapshots").glob("*.json")))
    assert snapshot.translations == {"e0": ""}


@pytest.mark.parametrize("field", ["entry_labels", "label_library"])
def test_legacy_metadata_edits_cannot_be_overwritten_by_frozen_save(qapp, monkeypatch, tmp_path, field):
    from transbridge.persistence.variant_store import VariantStore

    ctx, tasks, spec = _inputs(monkeypatch)
    ctx.uses_authoritative_projection = False
    ctx.slots = {"first": ctx.slots["first"]}
    ctx.active_project = SimpleNamespace(variant_dir=lambda _name: tmp_path)
    ctx.active_variant = "main"
    ctx.variant_store = VariantStore(tmp_path / "current.json")
    ctx.entry_labels, ctx.label_library = {"e0": {"old"}}, {"old": {"name": "旧标签"}}
    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    session = task_session.TaskSession(ctx, tasks, spec)
    assert support._capture(qapp, session)[0]
    session.mark_completed()
    getattr(ctx, field)["new"] = {"changed"} if field == "entry_labels" else {"name": "新标签"}
    with pytest.raises(RuntimeError, match="标签"):
        session.save_translation(on_success=lambda _: None, on_error=lambda _: None)
    assert not (tmp_path / "current.json").exists()


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

    def commit(states, context, **options):
        assert get_ident() == gui
        return OperationResult.completed({})

    ctx.project_commands = SimpleNamespace(save_snapshot=snapshot, save=save, replace_entry_states=commit)
    slot = ctx.slots["first"]
    tasks = (SourceTask("first", "First", None, slot.collection, tuple(slot.collection), ()),)
    monkeypatch.setattr(task_session, "VersionPersistence", VersionPersistence)
    session = task_session.TaskSession(ctx, tasks, SimpleNamespace(run_id="ports"))
    assert support._capture(qapp, session)[0]
    session.mark_completed()
    success, errors = support._save(qapp, session)
    assert success and not errors and [kind for kind, _ in calls] == ["snapshot", "save", "snapshot"]


@pytest.mark.parametrize("edit_after_commit", [False, True])
@pytest.mark.parametrize("reproject", [False, True])
def test_postcommit_projection_refresh_is_async_and_never_absorbs_user_edit(
    qapp, session, monkeypatch, edit_after_commit, reproject
):
    support._capture(qapp, session)
    threads, success, errors = [], [], []
    gui = get_ident()
    original_steps = SessionCapture.steps
    original_prepare = SessionCapture.prepare

    def steps(capture):
        if capture.run_id == "post-commit" and edit_after_commit:
            next(iter(session._ctx.collection)).translation = "提交之后的新编辑"
        yield from original_steps(capture)

    def prepare(capture):
        if capture.run_id == "post-commit":
            threads.append(get_ident())
        return original_prepare(capture)

    def commit(entries):
        if reproject:
            for key, slot in session._ctx.slots.items():
                slot.collection = TranslationEntryCollection(
                    replace(entry, revision=entry.revision.next())
                    for entry in entries
                    if entry.identity.namespace.value == key
                )
            session._ctx.variant_revision += 1
        return SimpleNamespace(is_success=True)

    monkeypatch.setattr(SessionCapture, "steps", steps)
    monkeypatch.setattr(SessionCapture, "prepare", prepare)
    monkeypatch.setattr(session._persistence, "commit_translation", commit)
    key = session.tasks[0].entries[0].identity
    session.tasks[0].entries[0].translation = "已应用候选"
    session.apply_entries_async({key}, on_success=success.append, on_error=errors.append)
    support._wait(qapp, session)
    assert success and not errors and session.applied_keys == frozenset({key})
    if edit_after_commit:
        assert next(iter(session._ctx.collection)).translation == "提交之后的新编辑"
        with pytest.raises(RuntimeError, match="再次修改"):
            session.require_current()
    else:
        assert threads and threads != [gui]
        session.require_current()
        assert next(iter(session._ctx.collection)).translation == "已应用候选"
