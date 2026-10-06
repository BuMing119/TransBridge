"""Own one AI execution independently of its progress window."""

from __future__ import annotations

from dataclasses import replace
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from .source_execution import SourceOutcome
from .task_completion import TaskCompletion
from .task_entry_results import TaskEntryResults
from .task_record_writer import TaskRecordWriter
from .task_recovery_binding import TaskRecoveryBinding
from .task_run_presentation import concise_event, source_label, summarize_entries
from .task_worker import AiTaskWorker

logger = logging.getLogger(__name__)


class AiTaskRun(QObject):
    changed = pyqtSignal()
    logged = pyqtSignal(str)
    preview_requested = pyqtSignal()
    translation_completed = pyqtSignal()
    completion_notice = pyqtSignal(object)

    def __init__(self, request, session, activity, *, client=None, project_id=None, consistency=None):
        super().__init__()
        self.request, self.session, self.activity = request, session, activity
        self.client, self.project_id = client, project_id
        self.consistency = consistency
        self.entries = TaskEntryResults(session.tasks, request.run_id)
        self._attempt_keys = set(self.entries.entries)
        self.applying = False
        self._attempt = 0
        self.blocked = False
        self.worker = None
        self.outcomes = {}
        self.log_paths = {}
        self.rows = {task.key: ("等待", f"{len(task.entries)} 条") for task in session.tasks}
        self.selected_key = session.tasks[0].key if session.tasks else None
        self.state = "preparing"
        self.status = "正在创建执行前快照"
        self.save_status = ""
        self.result_summary = ""
        self.snapshot_failed = False
        self.progress = (0, len(session.tasks), "已处理 %v/%m 个来源")
        self.pause_state = "running"
        self.cancelled = False
        self.user_cancelled = False
        self.completion_received = False
        self.preparing = True
        self.shutting_down = False
        self.records = TaskRecordWriter(request, session, self)
        self.records.changed.connect(self.changed)
        self.completion = TaskCompletion(self)
        self.recovery = TaskRecoveryBinding(request, session)
        self._project_saved_signal = getattr(session, "project_saved_signal", None)
        if self._project_saved_signal is not None:
            self._project_saved_signal.connect(self._observe_project_saved)

    @property
    def busy(self):
        return self.running or self.session.is_busy or self.records.busy or self.completion.pending is not None

    @property
    def running(self):
        return self.preparing or self.worker is not None or self.applying

    @property
    def can_retry(self):
        return (
            not self.running
            and not self.session.is_busy
            and bool(self.entries.failed_keys)
            and not self.cancelled
            and not self.blocked
            and not self.shutting_down
        )

    @property
    def can_apply_success(self):
        return (
            not self.running
            and not self.session.is_busy
            and bool(self.entries.ready_keys)
            and not self.cancelled
            and not self.blocked
            and not self.shutting_down
        )

    def prepare(self):
        self._record()
        try:
            self.session.capture_before(on_success=self._ready, on_error=self._prepare_failed)
        except Exception as exc:
            self._prepare_failed(str(exc))

    def _ready(self, _result):
        self.preparing = False
        if self.cancelled:
            self._finish_cancelled()
            return
        try:
            self._start(self.session.tasks)
        except Exception as exc:
            self._prepare_failed(str(exc))

    def _prepare_failed(self, error):
        self.preparing = False
        if self.cancelled:
            self._finish_cancelled()
            return
        self.state, self.status = "error", f"任务未启动：{error}"
        self.logged.emit("任务未启动")
        self.session.rollback_uncommitted()
        self.activity.fail(error)
        self._record()
        self.changed.emit()

    def _start(self, tasks):
        self._require_current()
        self.completion.reset()
        for task in tasks:
            self.log_paths[task.key] = None
        self._attempt_keys = {entry.identity for task in tasks for entry in task.entries}
        self._attempt += 1
        if self._attempt > 1:
            self.logged.emit(f"正在重试 {len(self._attempt_keys)} 条未完成条目")
        worker = AiTaskWorker(
            self.request,
            tasks,
            client=self.client,
            project_id=self.project_id,
            consistency=self.consistency,
            attempt_id=f"{self.request.run_id}-attempt-{self._attempt}",
            checkpoint_root=(
                self.session.project_dir / "ai-proofread-resume" if self.session.project_dir is not None else None
            ),
        )
        self.worker = worker
        self.completion_received = False
        self.state, self.status = "running", f"正在执行：{self.request.spec.execution_profile.summary}"
        self.result_summary = ""
        self.pause_state = "running"
        worker.source_started.connect(lambda key: self._source_started(key) if self.worker is worker else None)
        worker.progress.connect(lambda *args: self._progress(*args) if self.worker is worker else None)
        worker.log.connect(lambda key, text: self._source_log(key, text) if self.worker is worker else None)
        worker.log_ready.connect(lambda key, path: self._log_ready(key, path) if self.worker is worker else None)
        worker.pause_state_changed.connect(lambda state: self._pause_changed(state) if self.worker is worker else None)
        worker.completed.connect(lambda results: self._completed(results) if self.worker is worker else None)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.activity.bind_worker(worker)
        self._record()
        worker.start()
        self.changed.emit()

    def _source_started(self, key):
        self.selected_key = key
        self.rows[key] = ("执行中", "")
        self.logged.emit(f"{source_label(self.session.tasks, key)} · 开始处理")
        self.changed.emit()

    def _log_ready(self, key, path):
        self.log_paths[key] = path
        self.changed.emit()

    def _source_log(self, key, text):
        event = concise_event(text)
        if event is not None and not self.completion_received:
            self.logged.emit(f"{source_label(self.session.tasks, key)} · {event}")

    def _progress(self, key, stage, current, total, message):
        if self.cancelled or self.completion_received:
            return
        stage = {"proofread": "校对", "terms": "术语准备"}.get(stage, stage)
        self.rows[key] = (stage, f"{current}/{total} · {message}")
        if self.pause_state == "running":
            self.status = f"{source_label(self.session.tasks, key)} · {stage} · {message}"
        self.progress = (current, max(1, total), "当前步骤 %v/%m")
        self.activity.progress(
            sum(item.successful for item in self.outcomes.values()), len(self.session.tasks), self.status
        )
        self.changed.emit()

    def _pause_changed(self, state):
        if self.cancelled or self.completion_received:
            return
        previous = self.pause_state
        self.pause_state = state
        self.status = {"pausing": "正在暂停", "paused": "已暂停", "running": "正在执行"}[state]
        if previous != state:
            self.logged.emit("已继续" if state == "running" else self.status)
        self.changed.emit()

    def toggle_pause(self):
        if self.worker is None or self.completion_received or self.cancelled:
            return
        if self.worker.is_paused:
            self.worker.resume()
            self.activity.resume()
        else:
            self._pause_changed("pausing")
            self.worker.pause()
            self.activity.pause()

    def cancel(self):
        if self.session.completed or self.cancelled:
            return
        if not self.shutting_down:
            self.user_cancelled = True
        self.cancelled = True
        self.state, self.status = "cancelling", "正在取消"
        self.logged.emit("正在取消")
        self.session.rollback_uncommitted()
        self.activity.request_cancel()
        if self.worker is not None:
            self.worker.stop()
        if not self.preparing and (self.worker is None or self.completion_received):
            self._finish_cancelled()
        self.changed.emit()

    def _completed(self, outcomes):
        if self.completion_received or self.worker is None:
            return
        self.completion_received = True
        self.cancelled |= self.worker.was_cancelled
        self.entries.ingest(outcomes, self._attempt_keys, cancelled=self.cancelled)
        full_tasks = {task.key: task for task in self.session.tasks}
        for item in outcomes:
            previous = self.outcomes.get(item.task.key)
            self.outcomes[item.task.key] = replace(
                item,
                task=full_tasks[item.task.key],
                polish={**(previous.polish if previous else {}), **item.polish},
                failed_keys=tuple(
                    key.serialize() if key.original is not None else key.local_key
                    for key in self.entries.failed_keys
                    if self.entries.entries[key].source == item.task.key
                ),
            )
        self.progress = (len(self.outcomes), len(self.session.tasks), "已处理 %v/%m 个来源")
        self.logged.emit("处理结束：" + summarize_entries(self.entries.entries).counts_text)
        if self.cancelled:
            self._finish_cancelled()
        elif self.request.spec.execution_profile.preview_enabled and any(
            self.entries.entries[key].action == "polish" for key in self.entries.ready_keys
        ):
            self.state, self.status = "pending_confirmation", "待确认"
            self._record()
            self.preview_requested.emit()
        else:
            self.apply_results()
        self.changed.emit()

    def apply_results(self, preview=None):
        """Run visible preview decisions before one authoritative commit."""
        if (
            self.session.completed
            or self.cancelled
            or self.shutting_down
            or self.blocked
            or self.applying
            or (self.worker is not None and not self.completion_received)
            or self.session.is_busy
        ):
            return
        try:
            self._require_current()
            needs_preview = self.request.spec.execution_profile.preview_enabled and any(
                self.entries.entries[key].action == "polish" and self.entries.entries[key].decision == "pending"
                for key in self.entries.ready_keys
            )
            if needs_preview and preview is None:
                self.state, self.status = "pending_confirmation", "待确认"
                self._record()
                self.preview_requested.emit()
                self.changed.emit()
                return
            if preview is not None:
                if not preview():
                    self.cancel()
                    return
            # The modal preview runs another event loop; validate again after it.
            if self.cancelled or self.shutting_down:
                return
            self._require_current()
            self.entries.confirm_without_preview()
            selected = self.entries.publish_to_drafts(self.session.tasks)
            if selected:
                self.applying = True
                self.state, self.status = "applying", "正在应用结果"
                self.changed.emit()
                self.session.apply_entries_async(
                    selected,
                    on_success=lambda _: self.completion.applied(selected),
                    on_error=self._apply_failed,
                    validate=self._require_current,
                )
            else:
                self.completion.applied(selected)
        except Exception as exc:
            logger.exception("AI task result application failed")
            self._apply_failed(str(exc))
        self._record()
        self.changed.emit()

    def _apply_failed(self, error):
        self.applying = False
        if not self.cancelled:
            self.state, self.status = "failed", f"应用失败：{error}"
            self.logged.emit(self.status)
        self._record()
        self.changed.emit()

    def _worker_finished(self, worker):
        if self.worker is worker:
            if not self.completion_received:
                self._completed(tuple(SourceOutcome(task, error="工作线程异常结束") for task in worker.tasks))
            self.worker = None
        worker.deleteLater()
        if self.session.completed or self.cancelled or self.state in {"error", "pending_confirmation"}:
            self._close_client()
        self.changed.emit()

    def _finish_cancelled(self):
        self.cancelled = True
        self.state, self.status = "cancelled", "已取消"
        self.logged.emit("任务已取消")
        self.session.rollback_uncommitted()
        self.activity.request_cancel()
        self.activity.finish(cancelled=True)
        for key in self.rows:
            self.rows[key] = ("已取消", "") if key in self.outcomes else ("未处理", "")
        if self.worker is None:
            self._close_client()
        self._record()
        self.changed.emit()

    def retry(self):
        if not self.can_retry or self.shutting_down:
            return
        try:
            self._require_current()
            tasks = self.session.retry_entries(self.entries.failed_keys)
            for task in tasks:
                self.rows[task.key] = ("等待", f"{len(task.entries)} 条")
            self._start(tasks)
        except Exception as exc:
            self.status = f"无法重试：{exc}"
            logger.exception("AI task retry could not start")
            self.logged.emit("重试未能开始")
            self.changed.emit()

    def _require_current(self):
        try:
            self.session.require_current()
            if self.consistency is not None:
                self.consistency.require_current(self.request)
        except Exception:
            self.blocked = True
            raise

    def save(self):
        self.completion.save()

    def _observe_project_saved(self):
        observe = getattr(self.session, "observe_project_saved", None)
        if observe is not None and observe():
            if not self.cancelled:
                self.status = "已完成" if self.session.completed else "成功结果已保存 · 可重试失败条目"
            if not self.session.is_busy:
                self.save_status = "项目已保存 · 快照未创建"
            self._record()
            self.changed.emit()

    def _record(self):
        try:
            self.recovery.update(self)
        except Exception as exc:
            logger.exception("AI task recovery state could not be saved")
            self.logged.emit(f"恢复记录保存失败：{exc}；本轮结果仍按正常流程保存，请勿依赖退出恢复。")
        if self.state in {"completed", "partial", "failed", "cancelled", "error", "pending_confirmation"}:
            self.result_summary = summarize_entries(self.entries.entries).text(cancelled=self.cancelled)
            for key in self.rows:
                summary = summarize_entries(self.entries.entries, source=key)
                phase = "已取消" if self.cancelled else "处理结束"
                self.rows[key] = (phase, summary.counts_text)
        outcomes = tuple(self.outcomes.get(task.key, SourceOutcome(task)) for task in self.session.tasks)
        self.records.write(outcomes, state=self.state, entry_results=self.entries)

    def shutdown(self):
        self.shutting_down = True
        if not self.session.completed and not self.cancelled:
            self.cancel()

    def dispose(self):
        if self.busy:
            raise RuntimeError("AI 任务仍在结束中")
        self._close_client()
        self.activity.close()
        if self._project_saved_signal is not None:
            self._project_saved_signal.disconnect(self._observe_project_saved)
            self._project_saved_signal = None

    def _close_client(self):
        client, self.client = self.client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.exception("AI task remote client cleanup failed")
                self.logged.emit("远端连接关闭失败")
