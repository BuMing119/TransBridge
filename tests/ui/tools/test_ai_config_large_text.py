"""The task configuration remains useful at the real application text size."""

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools.smart_assistant.test_theme_migration import _snapshot
from tests.ui.tools.test_ai_translator_task_layout import _Callbacks
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView, WindowConfigView


class _PresetCallbacks(_Callbacks):
    def __init__(self):
        super().__init__()
        self.presets = []

    def on_preset(self, preset):
        self.presets.append(preset)


@pytest.mark.parametrize("point_size", [10, 14])
def test_large_text_narrow_task_pages_have_room_and_no_horizontal_scrolling(layout_app, point_size):
    parent = QWidget()
    callbacks = _PresetCallbacks()
    view = AITranslatorView(parent, callbacks)
    adapter = WindowConfigView(view, callbacks, lambda: None)
    adapter.render_config(LLMConfig())
    apply_layout_font(parent, _snapshot(ThemeScheme.LIGHT, 1), point_size)
    parent.resize(720, 560)
    parent.show()
    try:
        for index in range(4):
            view.controls.tabs.setCurrentIndex(index)
            settle_layout(layout_app)
            page = view.controls.tabs.currentWidget()
            assert view.controls.target_lang_combo.font().pointSizeF() == point_size
            assert parent.size().width() == 720 and parent.size().height() == 560
            assert page.viewport().height() >= 200
            assert page.horizontalScrollBar().maximum() == 0
        view.controls.concurrent_spin.setValue(7)
        view.controls.tabs.setCurrentIndex(0)
        settle_layout(layout_app)
        page = view.controls.tabs.currentWidget()
        preset_buttons = (
            view.controls.preset_untranslated,
            view.controls.preset_selection,
            view.controls.preset_table_view,
        )
        for width in (1120, 720, 1120, 720):
            parent.resize(width, 560)
            settle_layout(layout_app)
            assert page.horizontalScrollBar().maximum() == 0
            row = view.controls.preset_table_view.parentWidget()
            assert row.height() <= max(button.geometry().bottom() for button in preset_buttons) + 1
            assert min(button.y() for button in preset_buttons) == 0
        for control in (
            view.controls.naming_scheme_combo,
            view.controls.preset_table_view,
            view.controls.overwrite_check,
        ):
            page.ensureWidgetVisible(control)
            settle_layout(layout_app)
            assert page.viewport().rect().contains(control.mapTo(page.viewport(), QPoint(2, 2)))
        page.ensureWidgetVisible(view.controls.preset_table_view)
        settle_layout(layout_app)
        QTest.mouseClick(view.controls.preset_table_view, Qt.MouseButton.LeftButton)
        assert callbacks.presets == ["table_view"]
        view.controls.tabs.setCurrentIndex(3)
        assert adapter.update_config(LLMConfig()).max_concurrent == 7
    finally:
        parent.close()
        parent.deleteLater()
