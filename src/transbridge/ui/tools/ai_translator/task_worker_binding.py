"""Receive worker feedback on the GUI thread and isolate each execution attempt."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSlot

from .task_run_presentation import concise_event, source_label


class TaskWorkerBinding(QObject):
    """Keep high-frequency worker events separate from task lifecycle ownership."""

    def __init__(self, run, worker):
        super().__init__(run)
        self.run = run
        self.worker = worker
        self._timer = None
        self._labels = {task.key: source_label(run.session.tasks, task.key) for task in run.session.tasks}
        worker.source_started.connect(self._source_started)
        worker.progress.connect(self._progress)
        for name, slot in (
            ("project_name_progress", self._project_names),
            ("source_finished", self._source_finished),
        ):
            if (signal := getattr(worker, name, None)) is not None:
                signal.connect(slot)
        if (signal := getattr(worker, "log_batch", None)) is not None:
            signal.connect(self._logs)
        else:
            worker.log.connect(self._log)
        worker.log_ready.connect(self._log_ready)
        worker.pause_state_changed.connect(self._pause_changed)
        worker.completed.connect(self._completed)
        worker.finished.connect(self._finished)
        if callable(getattr(worker, "enable_feedback_polling", None)):
            worker.enable_feedback_polling()
            self._timer = QTimer(self)
            self._timer.setInterval(50)
            self._timer.timeout.connect(self._poll)
            self._timer.start()

    @pyqtSlot()
    def _poll(self):
        if self._current:
            self.worker.poll_feedback()
        elif self._timer is not None:
            self._timer.stop()

    @property
    def _current(self):
        return self.run.worker is self.worker

    @pyqtSlot(str)
    def _source_started(self, key):
        if self._current:
            self.run._source_started(key)

    @pyqtSlot(str, str, int, int, str)
    def _progress(self, key, stage, current, total, message):
        if self._current:
            self.run._progress(key, stage, current, total, message)

    @pyqtSlot(object)
    def _project_names(self, event):
        if self._current:
            self.run._project_name_progress(event)

    @pyqtSlot(str)
    def _source_finished(self, key):
        if self._current:
            self.run._source_finished(key)

    @pyqtSlot(str, str)
    def _log(self, key, text):
        if self._current:
            self.run._source_log(key, text)

    @pyqtSlot(object)
    def _logs(self, events):
        if not self._current or self.run.completion_received:
            return
        lines = [
            f"{self._labels.get(key, '处理来源')} · {event}"
            for key, text in events
            if (event := concise_event(text)) is not None
        ]
        if lines:
            self.run.logged.emit("\n".join(lines))

    @pyqtSlot(str, str)
    def _log_ready(self, key, path):
        if self._current:
            self.run._log_ready(key, path)

    @pyqtSlot(str)
    def _pause_changed(self, state):
        if self._current:
            self.run._pause_changed(state)

    @pyqtSlot(object)
    def _completed(self, outcomes):
        if self._current:
            self.run._completed(outcomes)

    @pyqtSlot()
    def _finished(self):
        if self._timer is not None:
            self._timer.stop()
            if self._current:
                self.worker.poll_feedback(all_pending=True)
        self.run._worker_finished(self.worker)
        self.deleteLater()
