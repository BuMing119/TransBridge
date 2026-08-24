"""Application-wide guards against accidental value changes from wheel input."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox


class AccidentalWheelGuard(QObject):
    """Prevent wheel events from silently changing collapsed choice/value fields."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QComboBox, QAbstractSpinBox)):
            return True
        return super().eventFilter(watched, event)


def install_accidental_wheel_guard(app: QApplication) -> AccidentalWheelGuard:
    """Install one process-wide guard and keep it owned by QApplication."""
    guard = AccidentalWheelGuard(app)
    app.installEventFilter(guard)
    return guard
