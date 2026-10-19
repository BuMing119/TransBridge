from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app  # noqa: F401
from tests.ui.test_ui_settings_dialog import _dialog, _preferences
from transbridge.config.llm import LLMConfig
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.theme_service import ThemePreference
from transbridge.ui.settings.sections import SettingsSection


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_settings_tab_visits_active_form_before_footer_and_returns_to_navigation(layout_app, tmp_path, mode):
    service, dialog = _dialog(
        layout_app,
        _preferences(tmp_path),
        llm_config=LLMConfig(),
        paratranz_config=SimpleNamespace(token="", base_url="https://example.invalid", timeout=30),
    )
    try:
        service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
        dialog.resize(720, 480)
        dialog.show()
        dialog.activateWindow()
        for section in SettingsSection:
            dialog.select_section(section)
            navigation = dialog._section_list
            navigation.setFocus()
            settle_layout(layout_app)
            page = dialog._stack.currentWidget()
            visited = []
            for _ in range(60):
                QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab)
                focused = layout_app.focusWidget()
                if focused is navigation:
                    break
                visited.append(focused)
            else:
                pytest.fail(f"Tab cannot leave {section.value}")

            assert visited and page.isAncestorOf(visited[0]), section.value
            assert visited[-2:] == [dialog._apply_button, dialog._cancel_button], section.value
            footer_reached = False
            for focused in visited:
                if focused in (dialog._default_button, dialog._apply_button, dialog._cancel_button):
                    footer_reached = True
                elif footer_reached:
                    pytest.fail(f"Tab returns to form after footer in {section.value}")
                else:
                    assert page.isAncestorOf(focused), section.value

            for expected in reversed(visited):
                QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
                assert layout_app.focusWidget() is expected, section.value
            QTest.keyClick(layout_app.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
            assert layout_app.focusWidget() is navigation
    finally:
        dialog.reject()
        dialog.deleteLater()
        service.close()
