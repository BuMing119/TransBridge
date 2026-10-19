from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QScrollArea
import pytest

from tests.ui.responsive_layout_support import (
    apply_layout_font,
    settle_layout,
    themed_layout_app,  # noqa: F401
)
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode, UiPreferenceRepository
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.settings.sections import SettingsSection
from transbridge.ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("point_size", [10, 14])
def test_settings_fit_narrow_window_and_keep_draft_through_theme_and_navigation(layout_app, tmp_path, point_size):
    path = tmp_path / "ui.ini"
    preferences = UiPreferenceRepository(
        ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    )
    registry = create_builtin_registry()
    service = ThemeService(layout_app, registry, preferences)
    service.start()
    source = LLMConfig()
    original_model = source.model
    dialog = SettingsDialog(service, preferences, registry=registry, llm_config=source)
    ai_page = dialog._settings_pages[SettingsSection.AI_SERVICE]
    try:
        dialog.resize(720, 480)
        dialog.show()
        dialog.select_section(SettingsSection.AI_SERVICE)
        ai_page.model_edit.selectAll()
        QTest.keyClicks(ai_page.model_edit, "draft-model")

        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            apply_layout_font(dialog, service.snapshot(), point_size)
            settle_layout(layout_app)
            assert ai_page.model_edit.font().pointSizeF() == point_size
            assert dialog.size().width() == 720
            navigation = dialog._section_list
            assert navigation.horizontalScrollBar().maximum() == 0
            for row in range(navigation.count()):
                item = navigation.item(row)
                item_rect = navigation.visualItemRect(item)
                assert navigation.viewport().rect().contains(item_rect)
                if navigation.fontMetrics().horizontalAdvance(item.text()) > item_rect.width():
                    assert item_rect.height() > navigation.fontMetrics().height()
                QTest.mouseClick(navigation.viewport(), Qt.MouseButton.LeftButton, pos=item_rect.center())
                settle_layout(layout_app)
                scroll = dialog._stack.currentWidget()
                assert isinstance(scroll, QScrollArea)
                assert scroll.horizontalScrollBar().maximum() == 0
                assert dialog._apply_button.isVisible()
                assert dialog.rect().contains(dialog._apply_button.mapTo(dialog, dialog._apply_button.rect().center()))
            assert ai_page.model_edit.text() == "draft-model"

        dialog.select_section(SettingsSection.AI_SERVICE)
        scroll = dialog._stack.currentWidget()
        scroll.ensureWidgetVisible(ai_page.prompt_cache_check)
        settle_layout(layout_app)
        checkbox_center = ai_page.prompt_cache_check.mapTo(
            scroll.viewport(), ai_page.prompt_cache_check.rect().center()
        )
        assert scroll.viewport().rect().contains(checkbox_center)
        previous = ai_page.prompt_cache_check.isChecked()
        QTest.mouseClick(ai_page.prompt_cache_check, Qt.MouseButton.LeftButton)
        assert ai_page.prompt_cache_check.isChecked() != previous
        QTest.mouseClick(dialog._cancel_button, Qt.MouseButton.LeftButton)
        assert not dialog.isVisible()
        assert source.model == original_model
    finally:
        dialog.reject()
        dialog.deleteLater()
        service.close()
