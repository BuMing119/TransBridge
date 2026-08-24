"""Small progress facade shared by the mixed legacy worker and task activity."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget


class AiMixedProgressWindow(QWidget):
    def __init__(self, worker: object, activity: object, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._activity = activity
        self._result_actions = None
        self.setWindowTitle("AI 混合运行 — 进行中")
        self.resize(440, 150)
        layout = QVBoxLayout(self)
        self._status = QLabel("准备中…")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._stop = QPushButton("停止")
        self._stop.clicked.connect(self._request_stop)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)
        layout.addWidget(self._stop)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.cancelled.connect(self._on_cancelled)

    def is_running(self) -> bool:
        return bool(self._worker.isRunning())

    @property
    def result_actions(self):
        return self._result_actions

    @property
    def task_activity(self):
        return self._activity.task_activity

    def set_result_actions(self, state: object) -> None:
        self._result_actions = state

    def _on_progress(self, value: object) -> None:
        current = int(getattr(value, "translate_done", 0)) + int(getattr(value, "polish_done", 0))
        total = int(getattr(value, "translate_total", 0)) + int(getattr(value, "polish_total", 0))
        if total:
            self._progress.setRange(0, total)
            self._progress.setValue(current)
        self._status.setText(str(getattr(value, "stage", "执行中")))

    def _request_stop(self) -> None:
        self._activity.request_cancel()
        self._worker.cancel()
        self._stop.setEnabled(False)
        self._status.setText("正在等待安全停止点")

    def _on_finished(self, _result: object) -> None:
        self._stop.setEnabled(False)
        self._status.setText("已完成")
        self._progress.setRange(0, 1)
        self._progress.setValue(1)

    def _on_error(self, message: str) -> None:
        self._stop.setEnabled(False)
        self._status.setText(f"失败：{message}")

    def _on_cancelled(self) -> None:
        self._stop.setEnabled(False)
        self._status.setText("已停止")

    def closeEvent(self, event) -> None:
        if self.is_running():
            self.hide()
            event.ignore()
            return
        event.accept()


__all__ = ["AiMixedProgressWindow"]
