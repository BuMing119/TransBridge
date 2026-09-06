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
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_progress
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


class _Worker(QObject):
    source_started = pyqtSignal(str)
    progress = pyqtSignal(str, str, int, int, str)
    log = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    finished = pyqtSignal()

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

    def publish(self, outcomes):
        self.completed.emit(tuple(outcomes))
        self.running = False
        self.finished.emit()

    def deleteLater(self):
        self.deleted = True


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
        self.commits = 0

    @property
    def can_save(self):
        return self.completed and not self.saved

    def capture_before(self, *, on_success, on_error):
        self.is_busy = True
        self.success_callback, self.error_callback = on_success, on_error

    def ready(self):
        self.is_busy = False
        self.success_callback({})

    def mark_completed(self):
        self.commits += 1
        self.completed = True

    def rollback_uncommitted(self):
        self.discarded = not self.completed

    def save_translation(self, *, on_success, on_error):
        self.saved = True
        on_success({})

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


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_reports(qapp, window):
    deadline = time.monotonic() + 5
    while window._reports_worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert window._reports_worker is None


@pytest.fixture
def make_window(qapp, monkeypatch):
    monkeypatch.setattr(task_progress, "AiTaskWorker", _Worker)
    monkeypatch.setattr(task_progress.QMessageBox, "information", lambda *_: None)
    rendered = []

    def render(outcome, request):
        rendered.append((outcome.task.key, threading.get_ident()))

    monkeypatch.setattr("transbridge.ui.tools.ai_translator.source_execution.render_source_report", render)
    windows = []

    def create(*, polish=False, preview=False):
        session = _Session(polish=polish)
        request = SimpleNamespace(
            spec=SimpleNamespace(execution_profile=SimpleNamespace(summary="翻译 → 校对", preview_enabled=preview))
        )
        window = task_progress.AiTaskProgressWindow(request, session, Mock())
        windows.append(window)
        return window, session, rendered

    yield create
    for window in windows:
        if window._worker is not None:
            window._worker.stop()
            window._worker.publish(())
        _wait_reports(qapp, window)
        window.session.is_busy = window._preparing = False
        window.close()


def _start(window, session):
    window.prepare()
    session.ready()
    return window._worker


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
    assert window.save_button.isEnabled()
    assert worker.deleted and window._worker is None
    assert sorted(key for key, _thread in rendered) == ["first", "second"]
    assert all(thread != threading.get_ident() for _key, thread in rendered)


def test_retry_only_failed_source_preserves_successful_detached_result(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    first, second = session.tasks
    next(iter(first.collection)).translation = "成功副本"
    next(iter(second.collection)).translation = "失败插件部分输出"
    worker.publish((SourceOutcome(first), SourceOutcome(second, error="network")))
    _wait_reports(qapp, window)
    assert session.commits == 0 and window.retry_button.isEnabled()
    window.retry_button.click()
    retried = window._worker
    assert tuple(task.key for task in retried.tasks) == (second.key,)
    assert next(iter(retried.tasks[0].collection)).translation == "旧译文"
    worker.completed.emit((SourceOutcome(second, error="late error"),))
    retried.publish((SourceOutcome(retried.tasks[0]),))
    _wait_reports(qapp, window)
    assert session.commits == 1
    assert next(iter(first.collection)).translation == "成功副本"
    assert window._outcomes["second"].successful


def test_cancel_during_execution_never_commits_late_success(qapp, make_window):
    window, session, _ = make_window()
    worker = _start(window, session)
    window.stop_button.click()
    assert session.discarded
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert session.commits == 0
    assert not window.retry_button.isEnabled()
    window.activity.finish.assert_called_with(cancelled=True)


def test_cancel_before_snapshot_completion_cannot_start_worker(make_window):
    window, session, _ = make_window()
    window.prepare()
    window.stop_button.click()
    session.is_busy = False
    session.error_callback("AI 任务已取消")
    assert window._worker is None and session.commits == 0
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
    assert all(not outcome.successful for outcome in window._outcomes.values())


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
    assert seen == ["first", "second"]
    assert session.commits == (0 if cancel_second else 1)
    if not cancel_second:
        assert [next(iter(task.collection)).translation for task in session.tasks] == ["first 译文", "second 译文"]


def test_preview_and_save_exceptions_are_visible_without_escaping_qt_slot(qapp, make_window, monkeypatch):
    window, session, _ = make_window()
    monkeypatch.setattr(window, "_apply_preview", Mock(side_effect=RuntimeError("preview failed")))
    worker = _start(window, session)
    worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    _wait_reports(qapp, window)
    assert session.commits == 0 and session.discarded
    assert "preview failed" in window.status.text()
    session.completed = True
    monkeypatch.setattr(session, "save_translation", Mock(side_effect=RuntimeError("version changed")))
    window._save()
    assert "version changed" in window.status.text()
    assert window.save_button.isEnabled()


def test_close_blocks_background_snapshot_and_report_lifetimes(qapp, make_window):
    window, session, _ = make_window()
    window.prepare()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    session.ready()
    window._worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    _wait_reports(qapp, window)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_real_qthread_completion_is_delivered_on_gui_thread_and_released(qapp, make_window, monkeypatch):
    class ThreadWorker(QThread):
        source_started = pyqtSignal(str)
        progress = pyqtSignal(str, str, int, int, str)
        log = pyqtSignal(str, str)
        completed = pyqtSignal(object)
        was_cancelled = False

        def __init__(self, request, tasks, **kwargs):
            super().__init__()
            self.tasks = tuple(tasks)

        def run(self):
            self.completed.emit(tuple(SourceOutcome(task) for task in self.tasks))

    monkeypatch.setattr(task_progress, "AiTaskWorker", ThreadWorker)
    window, session, rendered = make_window()
    calls = []
    commit = session.mark_completed

    def record_commit():
        calls.append(threading.get_ident())
        commit()

    session.mark_completed = record_commit
    _start(window, session)
    deadline = time.monotonic() + 5
    while window._worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert window._worker is None
    _wait_reports(qapp, window)
    assert calls == [threading.get_ident()]
    assert len(rendered) == 2


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
    window, session, _ = make_window()
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), list(session.tasks[0].entries))
    activity = controller.create_activity(request)
    window.activity = activity
    worker = _start(window, session)
    if exit_path == "cancel_preview":
        monkeypatch.setattr(window, "_apply_preview", lambda: False)
        worker.publish(tuple(SourceOutcome(task) for task in session.tasks))
    else:
        worker.publish(tuple(SourceOutcome(task, error="service failed") for task in session.tasks))
    _wait_reports(qapp, window)
    if exit_path == "abandon_failed_task":
        assert not activity.activity.is_terminal
        window.close()
    assert activity.activity.state is AiLegacyRunState.CANCELLED
    assert activity.activity.is_terminal and not session.completed
    controller.finish(request.run_id)
    activity.close()


@pytest.mark.parametrize("cleanup_before_close", [False, True])
def test_remote_client_closes_once_only_after_background_work_stops(make_window, cleanup_before_close):
    window, session, _ = make_window()
    client = Mock()
    window._client = client
    window.prepare()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    client.close.assert_not_called()
    window._preparing = session.is_busy = False
    if cleanup_before_close:
        window._close_client()
    window.closeEvent(QCloseEvent())
    window.closeEvent(QCloseEvent())
    client.close.assert_called_once_with()
