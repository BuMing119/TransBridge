from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_progress, task_run
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


class _Worker(QObject):
    source_started = pyqtSignal(str)
    progress = pyqtSignal(str, str, int, int, str)
    log = pyqtSignal(str, str)
    log_ready = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    finished = pyqtSignal()
    pause_state_changed = pyqtSignal(str)

    def __init__(self, request, tasks, **kwargs):
        super().__init__()
        self.tasks = tuple(tasks)
        self.was_cancelled = self.is_paused = self.running = self.deleted = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def stop(self):
        self.was_cancelled = True

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False
        self.pause_state_changed.emit("running")

    def publish(self, outcomes):
        from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome

        for outcome in outcomes:
            if not outcome.polish and outcome.task.polish_entries and not outcome.error:
                outcome.polish = {
                    entry.id: SimpleNamespace(
                        accepted=True,
                        confidence=1.0,
                        polished_translation=f"{outcome.task.key} 译文",
                        processing_status="completed",
                        verdict="pass",
                        note="",
                    )
                    for entry in outcome.task.polish_entries
                }
            if outcome.translation is None and outcome.task.translate_entries and not outcome.error:
                outcome.translation = SimpleNamespace(
                    entry_outcomes={
                        entry.identity: TranslationEntryOutcome(
                            entry.identity,
                            "succeeded",
                            outcome.task.collection.get(entry.identity).translation,
                            entry.stage,
                        )
                        for entry in outcome.task.translate_entries
                    }
                )
        self.completed.emit(tuple(outcomes))
        self.running = False
        self.finished.emit()

    def deleteLater(self):
        self.deleted = True


class _ProjectSignals(QObject):
    dirty_changed = pyqtSignal()


class _Session:
    def __init__(self, *, polish=False):
        tasks = []
        for name in ("first", "second"):
            entry = TranslationEntry(
                "same-id", "key", "original", "旧译文", 1, "NPC_:FULL", entry_key=EntryKey(SourceNamespace(name), "key")
            )
            collection = TranslationEntryCollection([entry])
            tasks.append(
                SourceTask(name, name, None, collection, () if polish else (entry,), (entry,) if polish else ())
            )
        self.tasks = tuple(tasks)
        self._before = {task.key: deepcopy(tuple(task.collection)) for task in tasks}
        self.completed = self.saved = self.is_busy = self.discarded = False
        self.project_saved = self.snapshot_saved = False
        self.external_project_saved = False
        self.project_signals = _ProjectSignals()
        self.project_saved_signal = self.project_signals.dirty_changed
        self.commits = 0
        self.applied_keys = set()

    @property
    def can_save(self):
        return bool(self.applied_keys) and not self.saved

    def capture_before(self, *, on_success, on_error):
        self.is_busy = True
        self.success_callback, self.error_callback = on_success, on_error

    def ready(self):
        self.is_busy = False
        self.success_callback({})

    def mark_completed(self):
        self.commits += 1
        self.completed = True

    def require_current(self):
        if self.discarded:
            raise RuntimeError("任务已取消")

    def apply_entries(self, keys):
        self.require_current()
        if set(keys) - self.applied_keys:
            self.commits += 1
            self.applied_keys.update(keys)
            self.saved = self.project_saved = self.snapshot_saved = False

    def apply_entries_async(self, keys, *, on_success, on_error, validate=None):
        if validate:
            validate()
        self.apply_entries(keys)
        on_success(None)

    def finish(self):
        self.completed = True

    def retry_entries(self, keys):
        sources = {task.key for task in self.tasks if any(entry.identity in keys for entry in task.entries)}
        self.reset_sources(sources)
        return tuple(
            replace(
                task,
                translate_entries=tuple(e for e in task.translate_entries if e.identity in keys),
                polish_entries=tuple(e for e in task.polish_entries if e.identity in keys),
            )
            for task in self.tasks
            if task.key in sources
        )

    def rollback_uncommitted(self):
        self.discarded = not self.completed

    def save_translation(self, *, on_success, on_error):
        self.saved = self.project_saved = self.snapshot_saved = True
        on_success({})

    def observe_project_saved(self):
        if self.completed and self.external_project_saved and not self.project_saved:
            self.project_saved = True
            return True
        return False

    def reset_sources(self, keys):
        tasks = []
        for task in self.tasks:
            if task.key not in keys:
                tasks.append(task)
                continue
            collection = TranslationEntryCollection(deepcopy(self._before[task.key]))
            tasks.append(
                replace(
                    task,
                    collection=collection,
                    translate_entries=tuple(collection) if task.translate_entries else (),
                    polish_entries=tuple(collection) if task.polish_entries else (),
                )
            )
        self.tasks = tuple(tasks)


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_reports(qapp, window):
    deadline = time.monotonic() + 5
    while (window.run.records.busy or window.run.completion.pending is not None) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert not window.run.records.busy


@pytest.fixture
def make_window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(task_run, "AiTaskWorker", _Worker)
    rendered = []
    save_record = TaskHistoryStore.save

    def save(store, record):
        rendered.append((record["state"], threading.get_ident()))
        return save_record(store, record)

    monkeypatch.setattr(TaskHistoryStore, "save", save)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.source_execution.render_source_report",
        Mock(side_effect=AssertionError("reports must only be exported on demand")),
    )
    windows = []

    def create(*, polish=False, preview=False):
        session = _Session(polish=polish)
        session.history_dir = tmp_path / f"history-{len(windows)}"
        session.project_dir = tmp_path / f"project-{len(windows)}"
        request = SimpleNamespace(
            run_id=f"test-{len(windows)}",
            config=SimpleNamespace(mixed_execution_order="sequential"),
            spec=SimpleNamespace(execution_profile=SimpleNamespace(summary="翻译 → 校对", preview_enabled=preview)),
        )
        window = task_progress.AiTaskProgressWindow(request, session, Mock())
        windows.append(window)
        return window, session, rendered

    yield create
    for window in windows:
        if window.run.worker is not None:
            window.run.worker.stop()
            window.run.worker.publish(())
        _wait_reports(qapp, window)
        window.session.is_busy = window.run.preparing = False
        window.dispose()


def _start(window, session):
    window.prepare()
    session.ready()
    return window.run.worker


def test_all_sources_commit_once_and_reports_run_off_gui_thread(qapp, make_window):
    window, session, rendered = make_window()
    worker = _start(window, session)
    outcomes = tuple(SourceOutcome(task) for task in session.tasks)
    notifications = []
    window.translation_completed.connect(lambda: notifications.append(True))
    worker.publish(outcomes)
    # A delayed duplicate from the finished worker must not restart publication/reporting.
    worker.completed.emit(outcomes)
    _wait_reports(qapp, window)
    assert session.commits == 1 and notifications == [True]
    assert session.saved and not window.save_button.isEnabled()
    assert worker.deleted and window.run.worker is None
    record = TaskHistoryStore(session.history_dir).load(window.request.run_id)
    assert record["state"] == "completed" and record["applied"]
    assert [source["key"] for source in record["sources"]] == ["first", "second"]
    assert all(thread != threading.get_ident() for _key, thread in rendered)


def test_retry_only_failed_source_preserves_successful_detached_result(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    first, second = session.tasks
    next(iter(first.collection)).translation = "成功副本"
    next(iter(second.collection)).translation = "失败插件部分输出"
    worker.publish((SourceOutcome(first), SourceOutcome(second, error="network")))
    _wait_reports(qapp, window)
    assert session.commits == 1 and session.saved and window.retry_button.isEnabled()
    window.retry_button.click()
    retried = window.run.worker
    assert tuple(task.key for task in retried.tasks) == (second.key,)
    assert next(iter(retried.tasks[0].collection)).translation == "旧译文"
    worker.completed.emit((SourceOutcome(second, error="late error"),))
    retried.publish((SourceOutcome(retried.tasks[0]),))
    _wait_reports(qapp, window)
    assert session.commits == 2 and session.saved
    window.run.apply_results()
    assert session.commits == 2
    assert next(iter(first.collection)).translation == "成功副本"
    assert window.run.outcomes["second"].successful


def test_cancel_during_execution_never_commits_late_success(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    window.stop_button.click()
    worker.progress.emit(session.tasks[0].key, "proofread", 10, 10, "迟到的校对进度")
    assert "正在取消" in window.status.text()
    assert session.discarded
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert session.commits == 0
    assert not window.retry_button.isEnabled()
    window.activity.finish.assert_called_with(cancelled=True)
    assert window.stop_button.text() == "关闭"
    assert all(row.text(1) == "已取消" for row in window.rows.values())


def test_step_progress_and_finished_close_do_not_cancel_success(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    worker.progress.emit(session.tasks[0].key, "proofread", 2, 5, "术语修复已处理 2/5 条")
    assert window.bar.value() == 2 and window.bar.maximum() == 5
    assert window.rows[session.tasks[0].key].text(1) == "校对"
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    assert window.stop_button.text() == "关闭"
    assert window.stop_button.isEnabled()
    _wait_reports(qapp, window)
    assert window.stop_button.text() == "关闭"
    window.stop_button.click()
    assert session.completed and not window.run.cancelled
    window.activity.request_cancel.assert_not_called()


def test_cancel_before_snapshot_completion_cannot_start_worker(make_window):
    window, session, _ = make_window()
    window.prepare()
    window.stop_button.click()
    session.is_busy = False
    session.error_callback("AI 任务已取消")
    assert window.run.worker is None and session.commits == 0
    window.activity.fail.assert_not_called()
    window.activity.finish.assert_called_with(cancelled=True)
    assert not window.is_running()


def test_unexpected_worker_exit_and_missing_sources_are_recoverable(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    worker.running = False
    worker.finished.emit()
    _wait_reports(qapp, window)
    assert session.commits == 0 and window.retry_button.isEnabled()
    assert all(not outcome.successful for outcome in window.run.outcomes.values())


@pytest.mark.parametrize("cancel_second", [False, True])
def test_preview_is_source_scoped_and_all_previews_precede_commit(qapp, make_window, monkeypatch, cancel_second):
    window, session, _ = make_window(polish=True, preview=True)
    seen = []

    class Preview:
        def __init__(self, entries, results, **kwargs):
            self.name = entries[0].identity.namespace.value
            seen.append(self.name)
            assert session.commits == 0

        def setWindowTitle(self, title):
            assert title.startswith(self.name)

        def exec(self):
            return (
                QDialog.DialogCode.Rejected if cancel_second and self.name == "second" else QDialog.DialogCode.Accepted
            )

        def get_results(self):
            return {"same-id": f"{self.name} 译文"}

    monkeypatch.setattr("transbridge.ui.tools.ai_translator._polish_preview_dialog._PolishPreviewDialog", Preview)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert seen == [] and session.commits == 0
    assert window.run.state == "pending_confirmation"
    window.show()
    window.preview_button.click()
    _wait_reports(qapp, window)
    assert seen == ["first", "second"]
    assert session.commits == (0 if cancel_second else 1)
    if not cancel_second:
        assert [next(iter(task.collection)).translation for task in session.tasks] == ["first 译文", "second 译文"]


def test_preview_and_save_exceptions_are_visible_without_escaping_qt_slot(qapp, make_window, monkeypatch):
    window, session, _ = make_window(polish=True, preview=True)
    monkeypatch.setattr(window, "_apply_preview", Mock(side_effect=RuntimeError("preview failed")))
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    window.show()
    window.preview_button.click()
    _wait_reports(qapp, window)
    assert session.commits == 0 and not session.discarded
    assert "preview failed" in window.status.text()
    session.completed = True
    session.applied_keys = {entry.identity for task in session.tasks for entry in task.entries}
    monkeypatch.setattr(session, "save_translation", Mock(side_effect=RuntimeError("version changed")))
    window.run.save()
    assert "version changed" in window.save_status.text()
    assert window.save_button.isEnabled()


def test_close_hides_while_snapshot_and_records_continue(qapp, make_window):
    window, session, _ = make_window()
    window.prepare()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert not session.discarded
    session.ready()
    window.run.worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    _wait_reports(qapp, window)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_real_qthread_completion_is_delivered_on_gui_thread_and_released(qapp, make_window, monkeypatch):
    class ThreadWorker(QThread):
        source_started = pyqtSignal(str)
        progress = pyqtSignal(str, str, int, int, str)
        log = pyqtSignal(str, str)
        log_ready = pyqtSignal(str, str)
        completed = pyqtSignal(object)
        pause_state_changed = pyqtSignal(str)
        was_cancelled = False

        def __init__(self, request, tasks, **kwargs):
            super().__init__()
            self.tasks = tuple(tasks)

        def run(self):
            from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome

            self.completed.emit(
                tuple(
                    SourceOutcome(
                        task,
                        translation=SimpleNamespace(
                            entry_outcomes={
                                entry.identity: TranslationEntryOutcome(
                                    entry.identity, "succeeded", entry.translation, entry.stage
                                )
                                for entry in task.entries
                            }
                        ),
                    )
                    for task in self.tasks
                )
            )

    monkeypatch.setattr(task_run, "AiTaskWorker", ThreadWorker)
    window, session, rendered = make_window()
    calls = []
    commit = session.apply_entries

    def record_commit(keys):
        calls.append(threading.get_ident())
        commit(keys)

    session.apply_entries = record_commit
    _start(window, session)
    deadline = time.monotonic() + 5
    while window.run.worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert window.run.worker is None
    _wait_reports(qapp, window)
    assert calls == [threading.get_ident()]
    assert any(state == "completed" for state, _thread in rendered)


@pytest.mark.parametrize("runtime_backed", [False, True])
@pytest.mark.parametrize("exit_path", ["cancel_preview", "abandon_failed_task"])
def test_real_activity_adapter_reaches_cancelled_terminal_state(
    qapp, make_window, monkeypatch, runtime_backed, exit_path
):
    from transbridge.application.tasks import TaskRuntime
    from transbridge.config.llm import LLMConfig
    from transbridge.ui.tools.ai_translator.run_controller import RunController
    from transbridge.ui.tools.ai_translator.task_adapter import AiLegacyRunState

    runtime = (
        TaskRuntime(
            id_generator=SimpleNamespace(new_id=lambda: "terminal-test"),
            clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        )
        if runtime_backed
        else None
    )
    window, session, _ = make_window(polish=exit_path == "cancel_preview", preview=exit_path == "cancel_preview")
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), list(session.tasks[0].entries))
    activity = controller.create_activity(request)
    window.activity = activity
    window.run.activity = activity
    worker = _start(window, session)
    if exit_path == "cancel_preview":
        monkeypatch.setattr(window, "_apply_preview", lambda: False)
        worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
        window.show()
        window.preview_button.click()
    else:
        worker.publish(tuple(SourceOutcome(task, error="service failed") for task in session.tasks))
    _wait_reports(qapp, window)
    if exit_path == "abandon_failed_task":
        assert not activity.activity.is_terminal
        window.close()
        assert not activity.activity.is_terminal
        window.run.cancel()
        _wait_reports(qapp, window)
    assert activity.activity.state is AiLegacyRunState.CANCELLED
    assert activity.activity.is_terminal and not session.completed
    controller.finish(request.run_id)
    activity.close()


@pytest.mark.parametrize("runtime_backed", [False, True])
def test_visible_preview_rejection_finishes_activity_and_persists_cancellation_reentrantly(
    qapp, make_window, monkeypatch, runtime_backed
):
    from transbridge.application.tasks import TaskRuntime
    from transbridge.config.llm import LLMConfig
    from transbridge.ui.tools.ai_translator.run_controller import RunController
    from transbridge.ui.tools.ai_translator.task_adapter import AiLegacyRunState

    runtime = (
        TaskRuntime(
            id_generator=SimpleNamespace(new_id=lambda: "visible-preview-test"),
            clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        )
        if runtime_backed
        else None
    )
    window, session, _ = make_window(polish=True, preview=True)
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), list(session.tasks[0].entries))
    activity = controller.create_activity(request)
    window.activity = window.run.activity = activity
    worker = _start(window, session)
    shown = []

    def reject():
        # The preview runs inside completed.emit(), before finished has cleared
        # the worker; this is different from reopening a hidden finished task.
        assert window.run.worker is worker and worker.isRunning()
        assert window.run.completion_received
        shown.append(True)
        return False

    monkeypatch.setattr(window, "_apply_preview", reject)
    window.show()
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)

    assert shown == [True]
    assert activity.activity.state is AiLegacyRunState.CANCELLED
    assert activity.activity.is_terminal
    assert window.run.state == "cancelled" and window.run.cancelled
    assert window.run.worker is None
    assert session.commits == 0 and session.discarded
    record = TaskHistoryStore(session.history_dir).load(window.request.run_id)
    assert record["state"] == "cancelled" and not record["applied"]
    assert window.run.records.record["state"] == "cancelled"
    controller.finish(request.run_id)
    activity.close()


def test_remote_client_closes_once_only_after_background_work_stops(qapp, make_window):
    window, session, _ = make_window()
    client = Mock()
    window.run.client = client
    window.prepare()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    client.close.assert_not_called()
    session.ready()
    client.close.assert_not_called()
    window.run.worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    window.closeEvent(QCloseEvent())
    window.closeEvent(QCloseEvent())
    client.close.assert_called_once_with()


def test_pause_feedback_survives_progress_and_window_reopen(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    window.show()
    window.pause_button.click()
    assert worker.is_paused
    assert window.status.text() == "正在暂停"
    assert window.pause_button.text() == "继续"
    worker.progress.emit("first", "proofread", 1, 5, "已处理 1 条")
    assert window.status.text() == "正在暂停"
    worker.pause_state_changed.emit("paused")
    assert window.status.text() == "已暂停"
    window.close()
    assert not window.isVisible() and not worker.was_cancelled
    assert not session.discarded
    window.show()
    assert window.status.text() == "已暂停"
    window.pause_button.click()
    assert not worker.is_paused and window.pause_button.text() == "暂停"
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert session.commits == 1


def test_history_write_failure_can_retry_without_rerunning_or_unapplying(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    original_save = TaskHistoryStore.save

    def fail(_store, _record):
        raise OSError("disk full")

    monkeypatch.setattr(TaskHistoryStore, "save", fail)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert session.commits == 1 and not session.discarded
    assert "disk full" in window.record_status.text()
    assert window.retry_record_button.isEnabled()
    assert window.stop_button.isEnabled() and session.saved
    window.close()
    assert not window.run.cancelled
    monkeypatch.setattr(TaskHistoryStore, "save", original_save)
    window.show()
    window.retry_record_button.click()
    _wait_reports(qapp, window)
    assert session.commits == 1 and window.run.worker is None
    assert TaskHistoryStore(session.history_dir).load(window.request.run_id)["applied"]
    assert not window.run.records.error


def test_closing_during_project_save_keeps_callback_and_result(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    callbacks = {}

    def delayed_save(*, on_success, on_error):
        session.is_busy = True
        callbacks["success"] = on_success

    monkeypatch.setattr(session, "save_translation", delayed_save)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    window.show()
    assert session.is_busy
    window.close()
    assert not window.isVisible() and not window.run.cancelled
    session.is_busy = False
    session.saved = session.project_saved = session.snapshot_saved = True
    callbacks["success"]({})
    _wait_reports(qapp, window)
    window.show()
    assert "已保存" in window.save_status.text()
    assert not window.save_button.isEnabled()
    assert TaskHistoryStore(session.history_dir).load(window.request.run_id)["saved"]


def test_snapshot_failure_offers_snapshot_retry_without_losing_saved_project(qapp, make_window, monkeypatch):
    window, session, _ = make_window()

    def fail_snapshot(*, on_success, on_error):
        session.project_saved = True
        on_error("snapshot unavailable")

    monkeypatch.setattr(session, "save_translation", fail_snapshot)
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert window.save_button.text() == "重试快照"
    assert window.save_button.isEnabled()
    assert "项目已保存" in window.save_status.text()
    assert "snapshot unavailable" in window.save_status.text()
    assert session.commits == 1 and not session.discarded
    record = TaskHistoryStore(session.history_dir).load(window.request.run_id)
    assert record["project_saved"] and not record["snapshot_saved"]


def test_close_and_reopen_remain_available_during_slow_history_write(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    entered, release = threading.Event(), threading.Event()
    original_save = TaskHistoryStore.save

    def delayed_save(store, record):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release history writer")
        return original_save(store, record)

    monkeypatch.setattr(TaskHistoryStore, "save", delayed_save)
    try:
        worker = _start(window, session)
        worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
        deadline = time.monotonic() + 2
        while not entered.is_set() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.001)
        assert entered.is_set() and window.run.records.busy
        window.show()
        window.close()
        assert not window.isVisible() and window.run.records.busy
        window.show()
        assert window.isVisible() and window.stop_button.isEnabled()
        assert session.saved and session.completed
        assert not window.run.cancelled
    finally:
        release.set()
        _wait_reports(qapp, window)
    assert TaskHistoryStore(session.history_dir).load(window.request.run_id)["state"] == "completed"


def test_external_autosave_refreshes_task_history_and_offers_first_snapshot(qapp, make_window, monkeypatch):
    window, session, writes = make_window()
    monkeypatch.setattr(session, "save_translation", lambda **callbacks: callbacks["on_error"]("save unavailable"))
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert "项目保存失败" in window.save_status.text()
    session.external_project_saved = True
    session.project_saved_signal.emit()
    _wait_reports(qapp, window)
    assert window.status.text() == "已完成"
    assert "项目已保存" in window.save_status.text()
    assert window.save_button.text() == "创建快照"
    assert window.save_button.isEnabled() and not session.saved
    record = TaskHistoryStore(session.history_dir).load(window.request.run_id)
    assert record["project_saved"] and not record["snapshot_saved"]
    assert not record["saved"] and record["applied"]
    write_count = len(writes)
    session.project_saved_signal.emit()
    qapp.processEvents()
    assert len(writes) == write_count


def test_dispose_disconnects_external_project_save_observer(qapp, make_window, monkeypatch):
    window, session, writes = make_window()
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    observer = Mock(wraps=session.observe_project_saved)
    monkeypatch.setattr(session, "observe_project_saved", observer)
    window.dispose()
    write_count = len(writes)
    session.external_project_saved = True
    session.project_saved_signal.emit()
    qapp.processEvents()
    observer.assert_not_called()
    assert len(writes) == write_count and not window.run.records.busy
    assert session.project_saved
