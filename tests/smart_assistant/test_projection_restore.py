"""Cleanup restores authority without reviving cancelled business permissions."""

from concurrent.futures import ThreadPoolExecutor
import queue
import threading
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from tests.smart_assistant.test_authoritative_entry_commit import _app_context, _Commands, _install_projection
from transbridge.application.io.publish import CommitDecision
from transbridge.application.tasks import TaskCleanupFailed
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.tool_translator import _tool_start_translation
from transbridge.smart_assistant.tools.types import ExecutionContext


def test_cancelled_gate_does_not_block_latest_authority_restore_or_reauthorize_writes():
    app = _app_context(_Commands())
    gate = SimpleNamespace(commit=lambda *_args: CommitDecision(False, "REQUEST_CANCELLED"))
    context = ExecutionContext(app_context=app, assistant_gate=gate, assistant_effect_id="cancelled-effect")
    before = context.capture_entry_states()
    _install_projection(app, translation="new authoritative translation")
    for entry in app.collection:
        entry.translation, entry.stage = "partial work", 1

    context.rollback_entry_states(before)

    assert all((entry.translation, entry.stage) == ("new authoritative translation", 3) for entry in app.collection)
    assert app.collection_changed.values == [app.collection]
    assert app.project_commands.calls == []
    assert context.assistant_gate is gate and context.assistant_effect_id == "cancelled-effect"
    with pytest.raises(RuntimeError, match="REQUEST_CANCELLED"):
        context.safe_mutate_wait(lambda: setattr(next(iter(app.collection)), "translation", "unauthorized"))
    assert all(entry.translation == "new authoritative translation" for entry in app.collection)


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("change", ["identity", "collection"])
def test_version_switch_before_or_after_enqueue_does_not_overwrite_either_view(queued, change):
    app = _app_context(_Commands())
    _install_projection(app)
    callbacks = queue.Queue()
    app.safe_mutate = callbacks.put
    context = ExecutionContext(app_context=app)
    original = app.collection
    before = context.capture_entry_states()
    for entry in original:
        entry.translation = "old view pending work"

    def switch():
        if change == "identity":
            app.active_version_identity = ("project", "other-variant")
        else:
            app.collection = make_test_collection(2)
        for entry in app.collection:
            entry.translation = "new view content"

    if queued:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(context.rollback_entry_states, before)
            callback = callbacks.get(timeout=2)
            switch()
            callback()
            future.result(timeout=2)
    else:
        switch()
        context.rollback_entry_states(before)
        assert callbacks.empty()

    assert all(entry.translation == "new view content" for entry in app.collection)
    if change == "collection":
        assert all(entry.translation == "old view pending work" for entry in original)
    assert not app.collection_changed.values and not app.project_commands.calls


@pytest.mark.parametrize("authority", ["missing", "incomplete"])
def test_missing_authority_fails_without_falling_back_to_captured_values(authority):
    app = _app_context(_Commands())
    context = ExecutionContext(app_context=app)
    before = context.capture_entry_states()
    if authority == "incomplete":
        original = app.collection
        app.collection = make_test_collection(1)
        _install_projection(app)
        app.collection = original
    for entry in app.collection:
        entry.translation = "partial work"

    with pytest.raises(RuntimeError, match="无法核验最新权威条目"):
        context.rollback_entry_states(before)
    assert all(entry.translation == "partial work" for entry in app.collection)
    assert not app.collection_changed.values and not app.project_commands.calls


def test_real_translation_cancel_cleanup_failure_preserves_cause_and_failed_notification(monkeypatch):
    app = _app_context(_Commands())
    app.esp_path, app.config = None, SimpleNamespace(token="")
    context = ExecutionContext(app_context=app)
    entered, release = threading.Event(), threading.Event()
    cleanup_error = OSError("authoritative restore unavailable")
    captured, notifications = [], []

    def rollback(*_args):
        raise cleanup_error

    context.rollback_entry_states = rollback

    class Translator:
        def __init__(self, *_args, **_kwargs):
            pass

        def translate(self, *, collection, **_kwargs):
            next(iter(collection)).translation = "partial translation"
            entered.set()
            assert release.wait(3)
            return SimpleNamespace(success_count=1, failed_count=0, skipped_count=0)

    start_thread = TaskManager.start_thread

    def launch(manager, task_id, target):
        def run():
            try:
                target()
            except TaskCleanupFailed as exc:
                captured.append(exc)
                raise

        return start_thread(manager, task_id, run)

    monkeypatch.setattr(TaskManager, "start_thread", launch)
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.LLMConfig.load_from_file",
        lambda: SimpleNamespace(api_key="test-key", term_priority=["dynamic"]),
    )
    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", Translator)
    monkeypatch.setattr("transbridge.ai_translator.translator.TranslatorConfig", lambda **kwargs: kwargs)
    TaskManager.reset()
    manager = TaskManager()
    manager.on_finished(lambda *args: notifications.append(args))
    try:
        result = _tool_start_translation({"entry_ids": [entry.key for entry in app.collection]}, context)
        assert result.success
        task_id = result.data["task_id"]
        worker = manager.get_handle(task_id)._thread
        try:
            assert entered.wait(3)
            assert manager.cancel(task_id)
        finally:
            release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert manager.get_status(task_id)["status"] == "failed"
        assert len(captured) == 1 and captured[0].__cause__ is cleanup_error
        assert notifications == [(task_id, False, str(captured[0]), None)]
        assert "authoritative restore unavailable" in notifications[0][2]
    finally:
        release.set()
        TaskManager.reset()
