from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QSpinBox

from transbridge.ui.input_guard import AccidentalWheelGuard

_APP = QApplication.instance() or QApplication([])


def _wheel_event(delta: int = -120) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_combo_box_wheel_does_not_change_selection() -> None:
    combo = QComboBox()
    combo.addItems(["男", "女"])
    combo.setCurrentIndex(0)
    guard = AccidentalWheelGuard(combo)
    combo.installEventFilter(guard)

    QApplication.sendEvent(combo, _wheel_event())

    assert combo.currentIndex() == 0


def test_spin_box_wheel_does_not_change_value() -> None:
    spin = QSpinBox()
    spin.setRange(0, 10)
    spin.setValue(5)
    guard = AccidentalWheelGuard(spin)
    spin.installEventFilter(guard)

    QApplication.sendEvent(spin, _wheel_event())

    assert spin.value() == 5
