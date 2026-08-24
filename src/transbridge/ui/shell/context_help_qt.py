"""Embeddable Qt panel for contextual help."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from transbridge.ui.shell.context_help import ContextHelpController, ContextHelpViewState


class ContextHelpPanel(QWidget):
    """Explains a topic inline without navigating away from the task page."""

    close_requested = pyqtSignal()

    def __init__(self, controller: ContextHelpController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setAccessibleName("功能与术语帮助")
        self._title = QLabel(self)
        self._purpose = QLabel(self)
        self._when = QLabel(self)
        self._purpose.setWordWrap(True)
        self._when.setWordWrap(True)
        self._title.setAccessibleName("帮助主题")
        self._purpose.setAccessibleName("用途说明")
        self._when.setAccessibleName("使用时机")
        for label in (self._title, self._purpose, self._when):
            label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._purpose)
        layout.addWidget(self._when)

    def show_topic(self, topic_id: str, *, context_identity: str) -> ContextHelpViewState:
        state = self._controller.show(topic_id, context_identity=context_identity)
        self._title.setText(state.topic.title)
        self._purpose.setText(f"用途：{state.topic.purpose}")
        self._when.setText(f"何时使用：{state.topic.when_to_use}")
        self._title.setFocus(Qt.FocusReason.OtherFocusReason)
        return state

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._controller.close()
        super().closeEvent(event)


__all__ = ["ContextHelpPanel"]
