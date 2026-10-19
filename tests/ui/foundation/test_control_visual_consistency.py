"""Shared controls retain contrast, keyboard cues and geometry across task surfaces."""

from collections import Counter
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QToolButton, QVBoxLayout, QWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.accessibility import contrast_ratio
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService


@pytest.fixture
def controls(layout_app):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    view = QWidget()
    layout = QVBoxLayout(view)
    buttons = []
    for kind in ("normal", "semantic", "task", "terminology", "destructive"):
        button = QPushButton("执行操作 Run", view)
        if kind != "terminology":
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        if kind == "semantic":
            ComponentStyle.apply_state(button, SemanticState.PRIMARY)
        elif kind == "task":
            button.setProperty("tbTaskPrimary", True)
        elif kind == "terminology":
            button.setProperty("tbTerminologyPrimary", True)
        elif kind == "destructive":
            ComponentStyle.apply_state(button, SemanticState.ERROR)
        layout.addWidget(button)
        buttons.append(button)
    view.resize(280, 260)
    view.show()
    view.activateWindow()
    settle_layout(layout_app)
    yield view, buttons, service
    view.close()
    view.deleteLater()
    settle_layout(layout_app)
    service.close()


def _colours(button):
    image = button.grab().toImage()
    return Counter(image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()))


def _surface(button):
    return button.grab().toImage().pixelColor(button.width() // 2, 4).name()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_primary_variants_share_contrast_and_disabled_appearance(layout_app, controls, mode):
    _view, buttons, service = controls
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    settle_layout(layout_app)
    foreground = layout_app.palette().color(QPalette.ColorRole.Base).name()
    background = layout_app.palette().color(QPalette.ColorRole.Link).name()
    assert contrast_ratio(foreground, background) >= 4.5
    assert len({button.sizeHint().height() for button in buttons}) == 1
    for button in buttons[1:4]:
        assert _surface(button) == background
        assert _colours(button)[foreground] > 5
        button.setCheckable(True)
        button.setChecked(True)
        settle_layout(layout_app)
        assert _surface(button) == background
        QTest.mouseMove(button, QPoint(button.width() // 2, button.height() // 2))
        settle_layout(layout_app)
        assert _colours(button)[foreground] > 5
    for button in buttons:
        button.setEnabled(False)
    settle_layout(layout_app)
    assert all(_surface(button) == _surface(buttons[0]) != background for button in buttons[1:])
    for button in buttons[1:4]:
        button.setEnabled(True)
    settle_layout(layout_app)
    assert all(_surface(button) == background for button in buttons[1:4])


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_pressed_secondary_action_retains_readable_text(layout_app, controls, mode):
    _view, buttons, service = controls
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    settle_layout(layout_app)
    button = buttons[0]
    foreground = layout_app.palette().color(QPalette.ColorRole.Text).name()
    background = layout_app.palette().color(QPalette.ColorRole.Highlight).name()
    QTest.mouseMove(button, button.rect().center())
    QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    try:
        settle_layout(layout_app)
        assert _surface(button) == background
        assert _colours(button)[foreground] > 5
        assert contrast_ratio(foreground, background) >= 4.5
    finally:
        QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=button.rect().center())


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_tab_focus_is_visible_without_resizing_and_space_activates_once(layout_app, controls, mode):
    view, buttons, service = controls
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    settle_layout(layout_app)
    buttons[0].setFocus(Qt.FocusReason.TabFocusReason)
    geometry = [button.geometry() for button in buttons]
    for button in buttons[1:4]:
        QTest.keyClick(view.focusWidget(), Qt.Key.Key_Tab)
        settle_layout(layout_app)
        assert view.focusWidget() is button
        border = button.grab().toImage().pixelColor(button.width() // 2, 0).name()
        assert border == layout_app.palette().color(QPalette.ColorRole.Text).name()
        clicked = []
        button.clicked.connect(lambda _checked=False, result=clicked: result.append(True))
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert clicked == [True]
        assert [item.geometry() for item in buttons] == geometry


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_destructive_secondary_action_has_visible_semantics_without_primary_fill(layout_app, controls, mode):
    _view, buttons, service = controls
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    settle_layout(layout_app)
    destructive = buttons[-1]
    expected = layout_app.palette().color(QPalette.ColorRole.BrightText).name()
    assert _colours(destructive)[expected] > 5
    assert _surface(destructive) == layout_app.palette().color(QPalette.ColorRole.Base).name()


def test_keyboard_toggle_keeps_checked_cue_across_theme_change_and_disabling(layout_app, controls):
    view, _buttons, service = controls
    toggle = QToolButton(view)
    toggle.setText("高级选项")
    toggle.setCheckable(True)
    ComponentStyle.apply_static(toggle, ComponentKind.BUTTON)
    view.layout().addWidget(toggle)
    toggle.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(toggle, Qt.Key.Key_Space)
    for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
        service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
        settle_layout(layout_app)
        assert toggle.isChecked()
        checked = _surface(toggle)
        assert checked == layout_app.palette().color(QPalette.ColorRole.Light).name()
        toggle.setEnabled(False)
        settle_layout(layout_app)
        assert toggle.isChecked() and _surface(toggle) != checked
        toggle.setEnabled(True)
        settle_layout(layout_app)
        assert _surface(toggle) == checked


def test_styling_already_polished_dialog_buttons_takes_effect_without_a_theme_switch(layout_app):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel, dialog)
    layout.addWidget(box)
    dialog.show()
    settle_layout(layout_app)
    try:
        for button in box.buttons():
            assert button.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        settle_layout(layout_app)
        assert {button.sizeHint().height() for button in box.buttons()} == {32}
    finally:
        dialog.close()
        dialog.deleteLater()
        settle_layout(layout_app)


def test_palette_refresh_failure_restores_existing_button_colours(layout_app, controls, monkeypatch):
    _view, buttons, service = controls
    before = service.snapshot()
    colours = [_surface(button) for button in buttons]
    refresh = service._refresh_palette_styles
    calls = 0

    def fail_after_refresh():
        nonlocal calls
        refresh()
        calls += 1
        if calls == 1:
            raise RuntimeError("injected button refresh failure")

    monkeypatch.setattr(service, "_refresh_palette_styles", fail_after_refresh)
    result = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    settle_layout(layout_app)
    assert result.snapshot is before
    assert "theme_apply_failed" in result.diagnostics
    assert [_surface(button) for button in buttons] == colours
