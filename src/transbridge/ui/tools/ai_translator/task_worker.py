"""One worker and lifecycle for AI tasks of any source count and mode."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from .source_execution import SourceExecutor


class AiTaskWorker(QThread):
    source_started = pyqtSignal(str)
    progress = pyqtSignal(str, str, int, int, str)
    log = pyqtSignal(str, str)
    log_ready = pyqtSignal(str, str)
    completed = pyqtSignal(object)
    pause_state_changed = pyqtSignal(str)

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
        while not self._monitor_done.wait(0.05):
            with self._control_lock:
                if self._stop.is_set() or self._pause.is_set() or self._pause_confirmed:
                    continue
                if self.request.request_budget.snapshot().in_flight == 0:
                    self._pause_confirmed = True
                    self.pause_state_changed.emit("paused")

    def run(self):
        from .source_execution import SourceOutcome

        outcomes = []
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
                progress=self.progress.emit,
                log=self.log.emit,
                log_ready=self.log_ready.emit,
                paratranz_client=self._client,
                project_id=self._project_id,
                **extra,
            )
            for task in self.tasks:
                self._pause.wait()
                if self._stop.is_set():
                    break
                self.source_started.emit(task.key)
                outcomes.append(executor.execute(task))
        except Exception as exc:
            remaining = self.tasks[len(outcomes) :]
            outcomes.extend(
                SourceOutcome(task, error=str(exc), failed_keys=tuple(e.key for e in task.entries))
                for task in remaining
            )
        finally:
            with self._control_lock:
                self._monitor_done.set()
            monitor.join()
        self.completed.emit(tuple(outcomes))
