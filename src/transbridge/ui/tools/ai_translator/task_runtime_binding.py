"""Deliver runtime cancellation on the owning Qt thread for an entire AI run."""

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from .task_adapter import AiLegacyRunState, TaskRuntimeAiTaskAdapter


class AiTaskRuntimeBinding(QObject):
    cancelled = pyqtSignal()

    def __init__(self, run):
        super().__init__(run)
        self.run = run
        self.activity = None
        self.closed = False
        # Always queue, even on the GUI thread: a commit's notification may
        # request cancellation before its caller has recorded accepted entries.
        self.cancelled.connect(self._cancel, Qt.ConnectionType.QueuedConnection)
        self.bind()

    def bind(self):
        activity = self.run.activity
        if activity is self.activity:
            return
        if self.activity is not None:
            self.activity.bind_run(None)
        self.activity = activity if isinstance(activity, TaskRuntimeAiTaskAdapter) else None
        if self.activity is not None:
            self.run.session.commit_mutation = self.activity.commit_mutation
            self.activity.bind_run(self.cancelled.emit)

    def require_active(self):
        self.bind()
        if self.activity is not None:
            if self.activity.is_cancelled:
                self.run.cancel()
            try:
                self.activity.require_active()
            except RuntimeError:
                if self.activity.activity.is_terminal:
                    self.run.blocked = True
                raise

    @property
    def is_cancelled(self):
        return self.activity is not None and self.activity.is_cancelled

    @property
    def is_failed(self):
        return self.activity is not None and self.activity.activity.state is AiLegacyRunState.FAILED

    def synchronize(self):
        self.bind()
        self._cancel()

    @pyqtSlot()
    def _cancel(self):
        if not self.closed and self.activity is not None and self.activity.is_cancelled:
            self.run.cancel()

    def close(self):
        self.closed = True
        if self.activity is not None:
            self.activity.bind_run(None)
