"""Bounded, keyboard-accessible option lists for AI multi-select filters."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


class FilterPopupMenu(QMenu):
    def __init__(self, parent, reset_text, reset) -> None:
        super().__init__(parent)
        self._reset_action = self.addAction(reset_text, reset)
        self._buttons: list[QAbstractButton] = []
        self._options = QWidget(self)
        self._layout = QVBoxLayout(self._options)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._options)
        action = QWidgetAction(self)
        action.setDefaultWidget(self._scroll)
        self.addAction(action)
        self.aboutToShow.connect(self._resize_options)

    def add_option(self, button: QAbstractButton) -> None:
        self._buttons.append(button)
        self._layout.addWidget(button)
        button.installEventFilter(self)

    def add_action_option(self, action: QAction) -> None:
        button = QToolButton(self._options)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setDefaultAction(action)
        button.clicked.connect(self.close)
        self.add_option(button)

    def _resize_options(self) -> None:
        for button in self._buttons:
            button.ensurePolished()
        sizes = [button.sizeHint().expandedTo(button.minimumSizeHint()) for button in self._buttons]
        for button, size in zip(self._buttons, sizes, strict=True):
            button.setFixedHeight(size.height())
        available = self.screen().availableGeometry()
        # Leave room for the reset action and keep large fonts within the screen.
        height = min(sum(size.height() for size in sizes[:12]), available.height() // 2)
        width = max((size.width() for size in sizes), default=0)
        width += self._scroll.verticalScrollBar().sizeHint().width()
        self._scroll.setFixedHeight(height)
        self._scroll.setMinimumWidth(min(width, available.width() - 40))

    def _navigate(self, key) -> bool:
        if key not in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End):
            return False
        buttons = [button for button in self._buttons if button.isEnabled()]
        if not buttons:
            return False
        current = next((index for index, button in enumerate(buttons) if button.hasFocus()), -1)
        if key == Qt.Key.Key_Up and current == 0:
            self.setFocus(Qt.FocusReason.PopupFocusReason)
            self.setActiveAction(self._reset_action)
            return True
        if key == Qt.Key.Key_Home:
            index = 0
        elif key == Qt.Key.Key_End:
            index = len(buttons) - 1
        else:
            index = max(0, min(current + (1 if key == Qt.Key.Key_Down else -1), len(buttons) - 1))
        buttons[index].setFocus(Qt.FocusReason.PopupFocusReason)
        self._scroll.ensureWidgetVisible(buttons[index])
        return True

    def keyPressEvent(self, event) -> None:
        if self._navigate(event.key()):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if watched not in self._buttons:
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.FocusIn:
            self._scroll.ensureWidgetVisible(watched)
        elif event.type() == QEvent.Type.KeyPress:
            if self._navigate(event.key()):
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                watched.click()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)
