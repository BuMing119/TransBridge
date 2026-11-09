"""Animation continuity, stable layout and lifecycle of the update action."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.shell.update_button import UpdateButton

_APP = QApplication.instance() or QApplication([])


@pytest.fixture(params=[ThemeMode.LIGHT, ThemeMode.DARK])
def button(request):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=request.param, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(_APP, create_builtin_registry(), preferences)
    service.start()
    widget = UpdateButton()
    widget.set_available(False)
    widget.resize(widget.sizeHint())
    widget.show()
    _APP.processEvents()
    widget.clearFocus()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()
    service.close()


def test_hover_reverses_from_current_frame_without_layout_shift(button):
    size = button.size()
    idle = button.grab().toImage()
    point = QPointF(5, 5)
    _APP.sendEvent(button, QEnterEvent(point, point, point))
    button._hover.setCurrentTime(60)
    middle = button._hover.currentValue()
    assert 0 < middle < 1
    assert button.grab().toImage() != idle
    _APP.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert button._hover.currentValue() == pytest.approx(middle)
    button._hover.setCurrentTime(180)
    assert button._hover.currentValue() == 0
    assert button.grab().toImage() == idle
    assert button.size() == size


def test_busy_rotation_stops_when_hidden_and_restores_available_state(button):
    size = button.sizeHint()
    button.set_available(True, "0.2.2")
    button.set_checking(True)
    assert button.sizeHint() == size
    assert button.text() == button.accessibleName() == "正在检查…"
    assert not button.isEnabled()
    first = button.grab().toImage()
    button._rotation.setCurrentTime(125)
    assert button.grab().toImage() != first
    button.hide()
    assert button._rotation.state() == QAbstractAnimation.State.Stopped
    button.show()
    assert button._rotation.state() == QAbstractAnimation.State.Running
    button.set_checking(False)
    assert button._rotation.state() == QAbstractAnimation.State.Stopped
    assert button.isEnabled()
    assert button.text() == "发现新版本"
    assert "0.2.2" in button.accessibleDescription()
    assert button.sizeHint() == size


def test_keyboard_press_animates_and_busy_blocks_duplicate_activation(button):
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    button.setFocus()
    QTest.keyPress(button, Qt.Key.Key_Space)
    button._press.setCurrentTime(50)
    assert 0 < button._press.currentValue() < 1
    QTest.keyRelease(button, Qt.Key.Key_Space)
    button._press.setCurrentTime(100)
    assert button._press.currentValue() == 0
    assert clicks == [True]
    button.set_checking(True)
    button.click()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert clicks == [True]
