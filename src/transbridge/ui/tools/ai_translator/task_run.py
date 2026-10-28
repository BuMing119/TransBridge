"""Own one AI execution independently of its progress window."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from .request_state_presentation import RequestStatePresentation
from .source_execution import SourceOutcome
from .task_activity import ActivityBatch
from .task_completion import TaskCompletion
from .task_progress_presentation import TaskProgressPresentation
from .task_record_writer import TaskRecordWriter
from .task_recovery_binding import TaskRecoveryBinding
from .task_result_flow import TaskResultFlow
from .task_result_preparation import TaskResultPreparation
from .task_run_presentation import concise_event, source_label
from .task_runtime_binding import AiTaskRuntimeBinding
from .task_worker import AiTaskWorker
from .task_worker_binding import TaskWorkerBinding

logger = logging.getLogger(__name__)


class AiTaskRun(QObject):
    changed = pyqtSignal()
    logged = pyqtSignal(object)
    preview_requested = pyqtSignal()
    translation_completed = pyqtSignal()
    completion_notice = pyqtSignal(object)

    def __init__(self, request, session, activity, *, client=None, project_id=None, consistency=None):
        super().__init__()
        self.request, self.session, self.activity = request, session, activity
        self.client, self.project_id = client, project_id
        self.consistency = consistency
        self.entries = session.initial_entry_results
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
        self.status = "正在保存任务输入"
        self.save_status = ""
        self.result_summary = ""
        self.request_state = RequestStatePresentation(
            getattr(getattr(request, "config", None), "max_concurrent", 1), self
        )
        self.progress = (0, len(session.tasks), "已处理 %v/%m 个来源")
        self.progress_view = TaskProgressPresentation(session.tasks)
        self.pause_state = "running"
        self.cancelled = False
        self.user_cancelled = False
        self.completion_received = False
        self.preparing = True
        self.shutting_down = False
        self.records = TaskRecordWriter(request, session, self)
        self.records.changed.connect(self.changed)
        self.completion = TaskCompletion(self)
        self.result_preparation = TaskResultPreparation(self)
        self.result_preparation.changed.connect(self.changed)
        self.result_flow = TaskResultFlow(self)
        self.recovery = TaskRecoveryBinding(request, session, parent=self, asynchronous=True)
        self.recovery.changed.connect(self.changed)
        self.recovery.failed.connect(lambda error: self.logged.emit(f"恢复记录保存失败：{error}；请勿依赖退出恢复。"))
        self.runtime_binding = AiTaskRuntimeBinding(self)
        self._project_saved_signal = getattr(session, "project_saved_signal", None)
        if self._project_saved_signal is not None:
            self._project_saved_signal.connect(self._observe_project_saved)

    @property
    def busy(self):
        return (
            self.running
            or self.session.is_busy
            or self.records.busy
            or self.recovery.busy
            or self.completion.pending is not None
        )

    @property
    def running(self):
        return self.preparing or self.worker is not None or self.applying or self.result_preparation.busy

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
        self.result_flow.prepare()

    def _ready(self, _result):
        try:
            self.result_flow.start(self.session.tasks)
        except Exception as exc:
            self._prepare_failed(str(exc))

    def _prepare_failed(self, error):
        self.preparing = False
        if self.consistency is not None and self.consistency.error:
            self.blocked = True
        if self.cancelled:
            self._finish_cancelled()
            return
        self.state, self.status = "error", f"任务未启动：{error}"
        self.logged.emit("任务未启动")
        self.session.rollback_uncommitted()
        self.activity.fail(error)
        if self.worker is None:
            self._close_client()
        self._record()
        self.changed.emit()

    def _start(self, tasks):
        self._require_current()
        self.completion.reset()
        for task in tasks:
            self.log_paths[task.key] = None
            self.rows[task.key] = ("等待", f"{len(task.entries)} 条")
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
        self.request_state.bind(worker)
        self.completion_received = False
        self.state, self.status = "running", f"正在执行：{self.request.spec.execution_profile.summary}"
        self.result_summary = ""
        self.progress_view.reset(tasks)
        self.progress = self.progress_view.progress
        self.pause_state = "running"
        self.worker_binding = TaskWorkerBinding(self, worker)
        self.activity.bind_worker(worker)
        self._record()
        worker.start()
        self.changed.emit()

    def _source_started(self, key):
        if self.cancelled or self.completion_received:
            return
        self.progress_view.source_started(key)
        self.progress = self.progress_view.progress
        self.activity.progress(*self.progress[:2], self.progress_view.summary)
        self.rows[key] = ("执行中", "")
        self.logged.emit(f"{source_label(self.session.tasks, key)} · 开始处理")
        self.changed.emit()

    def _log_ready(self, key, path):
        self.log_paths[key] = path
        self.changed.emit()

    def _source_log(self, key, text):
        event = concise_event(text)
        if event is not None and not self.completion_received:
            self.logged.emit(ActivityBatch(((source_label(self.session.tasks, key), event),)))

    def _progress(self, key, stage, current, total, message):
        if self.cancelled or self.completion_received:
            return
        if stage == "project_names" and self.progress_view.names_observed:
            return
        self.progress_view.source_progress(key, stage, current, total, message)
        row = self.progress_view.source_rows.get(key)
        if row is None:
            return
        self.rows[key] = (row.phase, f"{row.count_text} · {row.detail}")
        self.progress = self.progress_view.progress
        self.activity.progress(*self.progress[:2], self.progress_view.summary)
        self.changed.emit()

    def _project_name_progress(self, event):
        if self.cancelled or self.completion_received:
            return
        self.progress_view.project_names(event)
        self.progress = self.progress_view.progress
        self.activity.progress(*self.progress[:2], self.progress_view.summary)
        self.changed.emit()

    def _source_finished(self, key):
        if self.cancelled or self.completion_received:
            return
        self.progress_view.source_finished(key)
        self.progress = self.progress_view.progress
        self.activity.progress(*self.progress[:2], self.progress_view.summary)
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
            self.activity.resume()
        else:
            self._pause_changed("pausing")
            self.activity.pause()

    def cancel(self):
        if (
            self.cancelled
            or self.runtime_binding.is_failed
            or (self.session.completed and not self.runtime_binding.is_cancelled)
        ):
            return
        if not self.shutting_down:
            self.user_cancelled = True
        self.cancelled = True
        self.state, self.status = "cancelling", "正在取消"
        self.logged.emit("正在取消")
        self.session.rollback_uncommitted()
        self.activity.request_cancel()
        if not self.preparing and (self.worker is None or self.completion_received):
            self._finish_cancelled()
        self.changed.emit()

    def _completed(self, outcomes):
        self.result_flow.completed(outcomes)

    def apply_results(self, preview=None):
        self.result_flow.apply(preview)

    def _apply_failed(self, error):
        self.applying = False
        if self.consistency is not None and self.consistency.error:
            self.blocked = True
        self.runtime_binding.synchronize()
        if self.runtime_binding.is_failed:
            self.blocked = True
            self.session.rollback_uncommitted()
            if self.worker is None:
                self._close_client()
        if self.cancelled:
            self._finish_cancelled()
        if not self.cancelled:
            self.state, self.status = "failed", f"应用失败：{error}"
            self.logged.emit(self.status)
        self._record()
        self.changed.emit()

    def _worker_finished(self, worker):
        self.request_state.finish(worker)
        if self.worker is worker:
            if not self.completion_received:
                self._completed(tuple(SourceOutcome(task, error="工作线程异常结束") for task in worker.tasks))
            self.worker = None
        worker.deleteLater()
        self.activity.unbind_worker(worker)
        if self.cancelled and not self.applying:
            self._finish_cancelled()
        if self.session.completed or self.cancelled or self.state == "error" or self.runtime_binding.is_failed:
            self._close_client()
        self.changed.emit()

    def _finish_cancelled(self):
        if self.applying or self.worker is not None or self.result_preparation.busy or self.state == "cancelled":
            return
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
        self.result_flow.retry()

    def _require_current(self):
        self.runtime_binding.require_active()
        try:
            self.session.require_current()
            if self.consistency is not None and self.consistency.error:
                raise RuntimeError(self.consistency.error)
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
                self.save_status = "项目已保存"
            self._record()
            self.changed.emit()

    def _record(self):
        frozen = self.entries.freeze()
        try:
            self.recovery.update(self, frozen_entry_results=frozen)
        except Exception as exc:
            logger.exception("AI task recovery state could not be queued")
            self.logged.emit(f"恢复记录保存失败：{exc}；本轮结果仍按正常流程保存，请勿依赖退出恢复。")
        if self.state in {"completed", "partial", "failed", "cancelled", "error", "pending_confirmation"}:
            self.result_summary = self.entries.index.summary.text(cancelled=self.cancelled)
            for key, summary in self.entries.index.sources.items():
                self.rows[key] = ("已取消" if self.cancelled else "处理结束", summary.counts_text)
        outcomes = tuple(self.outcomes.get(task.key, SourceOutcome(task)) for task in self.session.tasks)
        self.records.write(outcomes, state=self.state, frozen_entry_results=frozen)

    def shutdown(self):
        self.shutting_down = True
        self.completion.reset()
        if not self.session.completed and not self.cancelled:
            self.cancel()

    def dispose(self):
        if self.busy:
            raise RuntimeError("AI 任务仍在结束中")
        self._close_client()
        if self.recovery.store is not None:
            self.recovery.store.release()
        self.runtime_binding.close()
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
