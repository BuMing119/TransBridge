"""One worker and lifecycle for AI tasks of any source count and mode."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from .source_execution import SourceExecutor


class AiTaskWorker(QThread):
    source_started = pyqtSignal(str)
    progress = pyqtSignal(str, str, int, int, str)
    log = pyqtSignal(str, str)
    completed = pyqtSignal(object)

    def __init__(self, request, tasks, *, client=None, project_id=None, executor_factory=SourceExecutor):
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

    @property
    def was_cancelled(self):
        return self._stop.is_set()

    @property
    def is_paused(self):
        return not self._pause.is_set()

    def stop(self):
        self._stop.set()
        self._pause.set()

    def pause(self):
        self._pause.clear()

    def resume(self):
        self._pause.set()

    def run(self):
        from .source_execution import SourceOutcome

        outcomes = []
        try:
            executor = self._factory(
                self.request,
                stop_event=self._stop,
                pause_event=self._pause,
                shared_terms=self._shared_terms,
                terms_lock=self._terms_lock,
                progress=self.progress.emit,
                log=self.log.emit,
                paratranz_client=self._client,
                project_id=self._project_id,
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
        self.completed.emit(tuple(outcomes))
