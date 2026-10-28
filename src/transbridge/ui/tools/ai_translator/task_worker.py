"""One worker and lifecycle for AI tasks of any source count and mode."""

from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from transbridge.application.translation.entry_alias import ai_entry_key

from .source_execution import SourceExecutor
from .task_feedback import TaskFeedback

logger = logging.getLogger(__name__)


class AiTaskWorker(QThread):
    source_started = pyqtSignal(str)
    source_finished = pyqtSignal(str)
    project_name_progress = pyqtSignal(object)
    progress = pyqtSignal(str, str, int, int, str)
    log = pyqtSignal(str, str)
    log_batch = pyqtSignal(object)
    log_ready = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    pause_state_changed = pyqtSignal(str)
    request_state_changed = pyqtSignal(object)

    def __init__(
        self,
        request,
        tasks,
        *,
        client=None,
        project_id=None,
        executor_factory=SourceExecutor,
        consistency=None,
        attempt_id=None,
        checkpoint_root=None,
    ):
        super().__init__()
        self.request = request
        self.tasks = tuple(tasks)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._shared_terms = {}
        self._terms_lock = threading.Lock()
        self._factory = executor_factory
        self._client = client
        self._project_id = project_id
        self._consistency = consistency
        self._attempt_id = attempt_id
        self._checkpoint_root = checkpoint_root
        self._control_lock = threading.Lock()
        self._monitor_done = threading.Event()
        self._pause_confirmed = False
        self._feedback = TaskFeedback(
            progress=self.progress.emit,
            project_progress=self.project_name_progress.emit,
            log_batch=self._emit_logs,
            request_state=self.request_state_changed.emit,
        )

    def enable_feedback_polling(self):
        self._feedback.enable_polling()

    def poll_feedback(self, *, all_pending=False):
        self._feedback.poll(all_pending=all_pending)

    def _emit_logs(self, batch):
        self.log_batch.emit(batch)
        # Existing consumers can keep their two-argument signal. New windows
        # subscribe only to log_batch and receive one event for many log lines.
        for key, text in batch:
            self.log.emit(key, str(text))

    @property
    def was_cancelled(self):
        return self._stop.is_set()

    @property
    def is_paused(self):
        return not self._pause.is_set()

    def stop(self):
        with self._control_lock:
            self._stop.set()
            self._pause.set()
        self.request.request_budget.notify_state_changed()

    def pause(self):
        with self._control_lock:
            if self._stop.is_set() or self._monitor_done.is_set():
                return
            self._pause_confirmed = False
            self._pause.clear()
            self.pause_state_changed.emit("pausing")
        self.request.request_budget.notify_state_changed()

    def resume(self):
        with self._control_lock:
            self._pause.set()
            self._pause_confirmed = False
            if not self._stop.is_set() and not self._monitor_done.is_set():
                self.pause_state_changed.emit("running")
        self.request.request_budget.notify_state_changed()

    def _monitor_pause(self):
        # A lease includes the provider's own retry loop. Confirm only after
        # those calls drain; pausing never cancels an admitted model call.
        previous = None
        while not self._monitor_done.wait(0.05):
            snapshot = self.request.request_budget.snapshot()
            if snapshot != previous:
                self._feedback.request_state(snapshot)
                previous = snapshot
            self._feedback.drain()
            with self._control_lock:
                if self._stop.is_set() or self._pause.is_set() or self._pause_confirmed:
                    continue
                if self.request.request_budget.snapshot().in_flight == 0:
                    self._pause_confirmed = True
                    self.pause_state_changed.emit("paused")

    def run(self):
        from transbridge.ai_translator.batch_execution import BatchExecutor

        from .source_dispatch import run_sources
        from .source_execution import SourceOutcome

        outcomes = []
        executor = None
        monitor = threading.Thread(target=self._monitor_pause, name="ai-task-pause", daemon=True)
        monitor.start()
        try:
            extra = {"consistency": self._consistency} if self._consistency is not None else {}
            if self._attempt_id is not None:
                extra["attempt_id"] = self._attempt_id
            if self._checkpoint_root is not None:
                extra["checkpoint_root"] = self._checkpoint_root
            executor = self._factory(
                self.request,
                stop_event=self._stop,
                pause_event=self._pause,
                shared_terms=self._shared_terms,
                terms_lock=self._terms_lock,
                progress=self._feedback.progress,
                log=self._feedback.log,
                log_ready=self.log_ready.emit,
                paratranz_client=self._client,
                project_id=self._project_id,
                **extra,
            )
            budget = self.request.request_budget
            with BatchExecutor(budget.max_in_flight + budget.max_preparing) as batches:
                if isinstance(executor, SourceExecutor):
                    executor.batch_executor = batches
                    executor.project_progress = self._feedback.project_progress
                    executor.prepare_names(self.tasks)
                outcomes = list(
                    run_sources(
                        self.tasks,
                        executor,
                        stop_event=self._stop,
                        pause_event=self._pause,
                        started=lambda key: self._feedback.barrier(self.source_started.emit, key),
                        finished=lambda key: self._feedback.barrier(self.source_finished.emit, key),
                        max_concurrent=self.request.request_budget.max_in_flight,
                    )
                )
                if isinstance(executor, SourceExecutor):
                    outcomes = list(executor.close_terms(outcomes))
        except Exception as exc:
            from .task_term_closure import fail_task_term_closure

            logger.exception("AI task or final terminology check failed")
            # A closure failure occurs after every source has returned. Keep
            # those candidates, but never report them as fully verified.
            outcomes = list(fail_task_term_closure(outcomes, str(exc)))
            finished = {outcome.task.key for outcome in outcomes}
            remaining = [task for task in self.tasks if task.key not in finished]
            outcomes.extend(
                SourceOutcome(task, error=str(exc), failed_keys=tuple(ai_entry_key(e) for e in task.entries))
                for task in remaining
            )
        finally:
            with self._control_lock:
                self._monitor_done.set()
            monitor.join()
            self._feedback.request_state(self.request.request_budget.snapshot())
        if isinstance(executor, SourceExecutor):
            from .task_name_reuse import retain_unfinished_reuse

            outcomes = retain_unfinished_reuse(executor, self.tasks, outcomes)
        self._feedback.finish(self.completed.emit, tuple(outcomes))
