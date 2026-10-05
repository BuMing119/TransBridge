from __future__ import annotations

from dataclasses import replace
import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.test_ai_task_session import _Context, _Persistence
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_run, task_session
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency
from transbridge.ui.tools.ai_translator.task_progress import AiTaskProgressWindow
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
        self.was_cancelled = self.is_paused = self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.was_cancelled = True

    def publish(self, outcome):
        self.completed.emit((outcome,))
        self.running = False
        self.finished.emit()

    def deleteLater(self):
        # Retain the signal sender so tests can reproduce queued late emissions.
        pass


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    assert predicate()


@pytest.fixture
def make_task(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(task_run, "AiTaskWorker", _Worker)
    monkeypatch.setattr(task_session, "VersionPersistence", _Persistence)
    windows = []

    def create(*, preview_enabled=True):
        ctx = _Context()
        entries = tuple(
            TranslationEntry(
                name,
                name,
                f"Source {name}",
                f"旧 {name}",
                1,
                "NPC_:FULL",
                entry_key=EntryKey(SourceNamespace("first"), name),
            )
            for name in ("translated", "failed", "polished", "rejected")
        )
        collection = TranslationEntryCollection(entries)
        ctx.slots["first"].collection = collection
        source = SourceTask("first", "First", None, collection, entries[:2], entries[2:])
        spec = SimpleNamespace(
            mode="mixed",
            run_id=f"partial-{len(windows)}",
            execution_profile=SimpleNamespace(summary="翻译 → 校对", preview_enabled=preview_enabled),
        )
        request = SimpleNamespace(run_id=spec.run_id, config=SimpleNamespace(model="model-one"), spec=spec)
        preferences = {"model": "model-one"}
        terms = SimpleNamespace(snapshot_identity="terms-v1")
        consistency = TaskConsistency(
            request,
            config_provider=lambda: preferences,
            terminology_provider=lambda: terms,
        )
        session = task_session.TaskSession(ctx, (source,), spec, project_dir=tmp_path / spec.run_id)
        window = AiTaskProgressWindow(request, session, Mock(), consistency=consistency)
        windows.append(window)
        window.prepare()
        _wait(qapp, lambda: window.run.worker is not None and not session.is_busy)
        return window, preferences, terms

    yield create
    for window in windows:
        if window.run.worker is not None:
            worker = window.run.worker
            window.run.cancel()
            worker.publish(SourceOutcome(worker.tasks[0], cancelled=True, error="任务已取消"))
        _wait(qapp, lambda: not window.run.busy)
        window.dispose()


def _outcome(worker, *, failed=True):
    task = worker.tasks[0]
    translation = SimpleNamespace(
        entry_outcomes={
            entry.identity: TranslationEntryOutcome(
                entry.identity,
                "failed" if failed and entry.id == "failed" else "succeeded",
                f"新 {entry.id}",
                3,
                "provider failed" if failed and entry.id == "failed" else "",
            )
            for entry in task.translate_entries
        }
    )
    polish = {
        entry.id: SimpleNamespace(
            accepted=True,
            confidence=1.0,
            polished_translation=f"新 {entry.id}",
            processing_status="completed",
            verdict="pass",
            note="",
        )
        for entry in task.polish_entries
    }
    return SourceOutcome(task, translation=translation, polish=polish, failed_keys=("failed",) if failed else ())


def _key(name):
    return EntryKey(SourceNamespace("first"), name)


def _live(window):
    return {entry.id: entry.translation for entry in window.session._ctx.collection}


def _apply_rejecting_one(window):
    def preview():
        window.run.entries.decide({_key("polished"): True, _key("rejected"): False})
        return True

    window.run.apply_results(preview)
    _settle(window)


def _settle(window):
    _wait(
        QApplication.instance(),
        lambda: (
            not window.run.applying
            and not window.session.is_busy
            and not window.run.records.busy
            and window.run.completion.pending is None
        ),
    )


def test_questionable_success_persists_stage_without_changing_text_or_joining_retry(qapp, make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    outcome = _outcome(worker)
    result = outcome.polish["polished"]
    result.target_stage = 2
    result.polished_translation = "旧 polished"
    result.note = "标记存在差异，已保留原译文并标为有疑问"
    worker.publish(outcome)

    row = window.run.entries.entries[_key("polished")]
    assert row.status == "succeeded" and row.stage == 2
    assert window.run.entries.failed_keys == {_key("failed")}
    _apply_rejecting_one(window)
    entry = window.session._ctx.collection.get(_key("polished"))
    assert entry.translation == "旧 polished" and entry.stage == 2
    _wait(qapp, lambda: not window.run.records.busy)
    record = TaskHistoryStore(window.session.history_dir).load(window.request.run_id)
    saved = next(
        item for item in record["sources"][0]["snapshot"]["entries"] if item["entry_key"]["local_key"] == "polished"
    )
    assert saved["stage"] == 2 and saved["accepted"]
    assert saved["report_details"]["result_status"] == "applied"


def test_partial_completion_requires_action_and_publishes_only_confirmed_success(qapp, make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    assert window.run.state == "pending_confirmation" and not window.session.applied_keys
    assert set(_live(window).values()) == {f"旧 {name}" for name in _live(window)}
    assert window.preview_button.text() == "确认结果" and window.preview_button.isEnabled()
    assert window.retry_button.text() == "重试失败条目" and window.retry_button.isEnabled()
    _apply_rejecting_one(window)
    assert _live(window) == {
        "translated": "新 translated",
        "failed": "旧 failed",
        "polished": "新 polished",
        "rejected": "旧 rejected",
    }
    assert window.session.applied_keys == {_key("translated"), _key("polished")}
    assert window.run.entries.failed_keys == {_key("failed")}
    assert window.run.entries.entries[_key("rejected")].decision == "rejected"
    assert window.session.saved and not window.session.can_save and not window.session.completed
    _wait(qapp, lambda: not window.run.records.busy)
    record = TaskHistoryStore(window.session.history_dir).load(window.request.run_id)
    details = {
        entry["entry_key"]["local_key"]: entry["report_details"]
        for entry in record["sources"][0]["snapshot"]["entries"]
    }
    assert details["translated"]["applied"] and details["polished"]["applied"]
    assert details["rejected"]["result_status"] == "rejected"
    assert details["failed"]["processing_status"] == "failed"


def test_partial_auto_save_then_retry_only_failed_and_auto_apply_again(qapp, make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    _apply_rejecting_one(window)
    persistence = window.session._persistence
    assert window.session.saved
    previous_snapshot = window.session.after_snapshot_name
    window.run.retry()
    retried = window.run.worker
    assert retried is not worker
    assert [entry.id for entry in retried.tasks[0].entries] == ["failed"]
    assert len(retried.tasks[0].collection) == 4
    assert retried.tasks[0].translate_entries[0].translation == "旧 failed"
    retried.publish(_outcome(retried, failed=False))
    _settle(window)
    assert _live(window)["failed"] == "新 failed"
    assert _live(window)["rejected"] == "旧 rejected"
    assert window.session.completed and window.session.saved
    assert window.session._persistence is not persistence
    assert window.session.after_snapshot_name != previous_snapshot
    assert window.run.entries.entries[_key("failed")].attempt == 2
    assert window.run.entries.entries[_key("translated")].attempt == 1
    assert window.run.entries.entries[_key("rejected")].attempt == 1


def test_duplicate_actions_and_late_completed_signal_cannot_reapply_or_overwrite(make_task):
    window, _, _ = make_task()
    first_worker = window.run.worker
    first_outcome = _outcome(first_worker)
    first_worker.publish(first_outcome)
    _apply_rejecting_one(window)
    persistence = window.session._persistence
    window.run.apply_results()
    assert len(persistence.commits) == 1
    window.run.retry()
    retry_worker = window.run.worker
    window.run.retry()
    assert window.run.worker is retry_worker
    first_worker.completed.emit((replace(first_outcome, error="stale"),))
    assert not window.run.completion_received
    assert window.run.entries.entries[_key("translated")].attempt == 1
    retry_worker.publish(_outcome(retry_worker, failed=False))
    _settle(window)
    final_persistence = window.session._persistence
    window.run.apply_results()
    retry_worker.completed.emit((SourceOutcome(retry_worker.tasks[0], error="late failure"),))
    assert len(final_persistence.commits) == 1
    assert _live(window)["failed"] == "新 failed"
    assert not window.run.entries.failed_keys


def test_previous_worker_progress_pause_and_logs_cannot_change_retry_view(make_task):
    window, _, _ = make_task()
    first = window.run.worker
    first.publish(_outcome(first))
    window.run.retry()
    current = window.run.worker
    current.progress.emit("first", "校对", 1, 2, "当前重试")
    previous = (window.run.status, window.run.progress, dict(window.run.rows), window.run.pause_state)
    first.source_started.emit("first")
    first.progress.emit("first", "校对", 99, 100, "旧请求进度")
    first.pause_state_changed.emit("paused")
    first.log.emit("first", "旧请求日志")
    assert (window.run.status, window.run.progress, window.run.rows, window.run.pause_state) == previous
    assert "旧请求日志" not in window.logs.toPlainText()


def test_direct_apply_cannot_skip_required_polish_confirmation(make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    window.run.apply_results()
    assert window.run.state == "pending_confirmation"
    assert not window.session.applied_keys
    assert _live(window)["translated"] == "旧 translated"
    assert _live(window)["polished"] == "旧 polished"


def test_partial_without_preview_automatically_applies_and_saves_successes(make_task):
    window, _, _ = make_task(preview_enabled=False)
    notices = []
    window.run.completion_notice.connect(notices.append)
    worker = window.run.worker
    worker.publish(_outcome(worker))
    _settle(window)

    assert window.run.state == "partial"
    assert window.session.saved
    assert window.session.applied_keys == {_key("translated"), _key("polished"), _key("rejected")}
    assert _live(window)["failed"] == "旧 failed"
    assert window.run.entries.failed_keys == {_key("failed")}
    assert window.run.can_retry and not window.run.can_apply_success
    assert len(notices) == 1 and not notices[0]["error"]
    assert "失败 1 条" in notices[0]["text"]


def test_questionable_success_without_preview_is_applied_and_saved(make_task):
    window, _, _ = make_task(preview_enabled=False)
    worker = window.run.worker
    outcome = _outcome(worker, failed=False)
    outcome.polish["polished"].target_stage = 2
    outcome.polish["polished"].polished_translation = "旧 polished"
    notices = []
    window.run.completion_notice.connect(notices.append)
    worker.publish(outcome)
    _settle(window)

    current = window.session._ctx.collection.get(_key("polished"))
    assert current.stage == 2 and current.translation == "旧 polished"
    assert window.session.saved and window.session.completed
    assert not window.run.entries.failed_keys
    assert len(notices) == 1 and "有疑问 1 条" in notices[0]["text"]
    assert not notices[0]["error"]


def test_automatic_save_failure_keeps_applied_results_and_retries_only_save(make_task):
    window, _, _ = make_task(preview_enabled=False)
    persistence = window.session._persistence
    persistence.fail_save = True
    worker = window.run.worker
    worker.publish(_outcome(worker))
    _settle(window)

    assert window.session.can_save and not window.session.saved
    assert "项目保存失败" in window.run.save_status
    assert len(persistence.commits) == 1 and len(persistence.saves) == 1
    assert _live(window)["translated"] == "新 translated"
    applied = window.session.applied_keys
    persistence.fail_save = False
    window.run.save()
    _settle(window)
    assert window.session.saved and window.session.applied_keys == applied
    assert len(persistence.commits) == 1 and len(persistence.saves) == 2
    assert window.run.entries.entries[_key("translated")].attempt == 1


def test_cancel_while_automatic_apply_is_preparing_never_commits_candidates(make_task):
    window, _, _ = make_task(preview_enabled=False)
    worker = window.run.worker
    worker.publish(_outcome(worker))
    assert window.run.applying
    window.run.cancel()
    _settle(window)

    assert window.run.cancelled and window.run.state == "cancelled"
    assert not window.session.applied_keys
    assert window.session._persistence.commits == []
    assert window.session._persistence.saves == []
    assert all(text.startswith("旧 ") for text in _live(window).values())


def test_cancel_preview_discards_every_unapplied_candidate(make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    window.run.apply_results(lambda: False)
    assert window.run.cancelled and not window.session.applied_keys
    assert _live(window)["translated"] == "旧 translated"
    assert _live(window)["polished"] == "旧 polished"
    assert not window.run.can_retry and not window.run.can_apply_success
    window.run.retry()
    window.run.apply_results()
    assert window.run.worker is None and window.session._persistence.commits == []


def test_cancel_during_modal_preview_keeps_cancelled_state(make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))

    def preview():
        window.run.cancel()
        return True

    window.run.apply_results(preview)
    assert window.run.state == "cancelled" and not window.run.blocked
    assert not window.session.applied_keys


def test_interrupted_entry_without_task_cancellation_is_retryable(make_task):
    window, _, _ = make_task()
    worker = window.run.worker
    outcome = _outcome(worker)
    result = outcome.translation.entry_outcomes[_key("failed")]
    outcome.translation.entry_outcomes[_key("failed")] = replace(result, status="cancelled")
    worker.publish(outcome)
    assert not window.run.cancelled
    assert window.run.entries.failed_keys == {_key("failed")}
    assert window.run.can_retry


@pytest.mark.parametrize("accept_dialog", [True, False])
def test_apply_button_previews_only_successful_polish_entries(make_task, monkeypatch, accept_dialog):
    from transbridge.ui.tools.ai_translator import _polish_preview_dialog

    window, _, _ = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    presented = []

    def dialog(entries, results, **kwargs):
        presented.extend(entry.id for entry in entries)
        preview = Mock()
        preview.exec.return_value = 1 if accept_dialog else 0
        preview.get_results.return_value = {"polished": results["polished"]}
        return preview

    monkeypatch.setattr(_polish_preview_dialog, "_PolishPreviewDialog", dialog)
    window.preview_button.click()
    _settle(window)
    assert presented == ["polished", "rejected"]
    assert not window._preview_open
    if accept_dialog:
        assert _live(window)["translated"] == "新 translated"
        assert _live(window)["polished"] == "新 polished"
        assert _live(window)["rejected"] == "旧 rejected"
        assert window.run.entries.entries[_key("rejected")].decision == "rejected"
    else:
        assert window.run.cancelled and not window.session.applied_keys
        assert _live(window)["translated"] == "旧 translated"


def test_cancel_retry_preserves_previously_applied_but_drops_late_success(make_task):
    window, _, _ = make_task()
    first = window.run.worker
    first.publish(_outcome(first))
    _apply_rejecting_one(window)
    window.run.retry()
    retry = window.run.worker
    window.run.cancel()
    retry.publish(_outcome(retry, failed=False))
    assert window.run.cancelled and window.run.state == "cancelled"
    assert _live(window)["translated"] == "新 translated"
    assert _live(window)["polished"] == "新 polished"
    assert _live(window)["failed"] == "旧 failed"
    assert _live(window)["rejected"] == "旧 rejected"
    assert window.session.saved and not window.session.can_save
    assert not window.run.can_apply_success and not window.run.can_retry


@pytest.mark.parametrize("operation", ["apply", "retry"])
@pytest.mark.parametrize("change", ["input", "config", "terms"])
def test_changed_inputs_or_execution_identity_block_partial_actions(make_task, change, operation):
    window, preferences, terms = make_task()
    worker = window.run.worker
    worker.publish(_outcome(worker))
    if change == "input":
        window.session._ctx.collection.get(_key("translated")).original = "用户修改原文"
    elif change == "config":
        preferences["model"] = "different-model"
    else:
        terms.snapshot_identity = "terms-v2"
    if operation == "apply":
        window.run.apply_results()
    else:
        window.run.retry()
    assert window.run.blocked
    assert not window.session.applied_keys and window.run.worker is None
    assert not window.run.can_apply_success and not window.run.can_retry
    assert _live(window)["translated"] == "旧 translated"
