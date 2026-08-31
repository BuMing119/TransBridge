"""A retained, non-modal entry editor with one close guard for X, Escape and the button."""

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from .view import DialogueEditorView


class EntryEditorDialog(QDialog):
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget, *, can_close: Callable[[], bool]) -> None:
        super().__init__(parent)
        self._can_close = can_close
        self.setWindowTitle("词条编辑")
        self.setWindowFlag(Qt.WindowType.WindowMinMaxButtonsHint, True)
        self.setModal(False)
        self.resize(1280, 820)
        self.setMinimumSize(820, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = DialogueEditorView(self)
        layout.addWidget(self.view)
        self.view.close_requested.connect(self.close)

    def reject(self) -> None:
        # QDialog's default Escape handling bypasses closeEvent.
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._can_close():
            event.ignore()
            return
        self.dismissed.emit()
        event.accept()
