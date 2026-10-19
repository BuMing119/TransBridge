from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QGroupBox, QWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app  # noqa: F401
from tests.ui.tools.test_ai_translator_task_layout import _Callbacks
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView, WindowConfigView
from transbridge.ui.tools.ai_translator.view_state import TranslatorViewPort


@pytest.fixture
def quality_view(layout_app):
    parent = QWidget()
    parent.resize(1120, 760)
    callbacks = _Callbacks()
    view = AITranslatorView(parent, callbacks)
    adapter = WindowConfigView(view, callbacks, lambda: None)
    port = TranslatorViewPort(view)
    view.controls.pp_enable_check.toggled.connect(port.update_post_process_controls)
    view.controls.pp_strategy_combo.currentIndexChanged.connect(port.update_post_process_controls)
    view.controls.pp_polish_check.toggled.connect(port.update_polish_controls)
    adapter.render_config(LLMConfig())
    view.controls.tabs.setCurrentIndex(2)
    parent.show()
    settle_layout(layout_app)
    yield parent, view, adapter
    parent.close()
    parent.deleteLater()
    settle_layout(layout_app)


def test_proofread_only_shows_effective_options_and_preserves_strict_choices(quality_view, layout_app):
    _parent, view, adapter = quality_view
    controls = view.controls
    adapter.render_config(
        LLMConfig(
            pp_strategy="strict",
            pp_enable_consistency_check=False,
            pp_enable_polish=True,
            pp_polish_scope="passed",
            pp_polish_level="aggressive",
            pp_strict_arbitration=True,
            polish_preview_enabled=True,
        )
    )
    controls.pp_strategy_combo.setCurrentIndex(0)
    settle_layout(layout_app)

    assert not controls.pp_stages.isVisible()
    assert not controls.pp_consistency_check.isVisible()
    assert not controls.pp_polish_scope_combo.isVisible()
    assert not controls.pp_polish_scope_label.isVisible()
    assert controls.pp_polish_level_combo.isVisible() and controls.pp_polish_level_combo.isEnabled()
    assert controls.polish_preview_check.isVisible() and controls.polish_preview_check.isChecked()
    assert "同一阶段" in controls.pp_strategy_note.text()
    controls.pp_polish_level_combo.setCurrentIndex(0)

    controls.pp_strategy_combo.setCurrentIndex(1)
    settle_layout(layout_app)
    assert controls.pp_stages.isVisible()
    assert controls.pp_polish_scope_combo.isVisible() and controls.pp_polish_scope_label.isVisible()
    assert not controls.pp_consistency_check.isChecked()
    assert controls.pp_strict_mode_check.isChecked()
    config = adapter.update_config(LLMConfig())
    assert config.pp_strategy == "strict"
    assert config.pp_polish_scope == "passed"
    assert config.pp_polish_level == "light"
    assert config.pp_enable_polish and config.polish_preview_enabled
    assert "耗时" in controls.pp_strategy_note.text()


def test_disabling_quality_preserves_options_and_keeps_preview_available(quality_view, layout_app):
    _parent, view, adapter = quality_view
    controls = view.controls
    adapter.render_config(LLMConfig(pp_strategy="strict", pp_enable_polish=True, polish_preview_enabled=True))
    controls.pp_enable_check.setChecked(False)
    settle_layout(layout_app)
    assert not controls.pp_stages.isVisible()
    assert not controls.pp_polish_level_combo.isEnabled()
    assert controls.polish_preview_check.isVisible() and controls.polish_preview_check.isEnabled()
    assert "已关闭" in controls.pp_strategy_note.text()

    controls.pp_enable_check.setChecked(True)
    settle_layout(layout_app)
    assert controls.pp_stages.isVisible()
    assert controls.pp_polish_check.isChecked()
    assert controls.pp_polish_level_combo.isEnabled()
    assert controls.polish_preview_check.isChecked()


@pytest.mark.parametrize("width,font_size", [(720, 9), (1120, 9), (1120, 14), (1440, 12)])
@pytest.mark.parametrize("strategy", ["proofread", "strict"])
def test_quality_page_never_needs_horizontal_scrolling(quality_view, layout_app, width, font_size, strategy):
    parent, view, adapter = quality_view
    parent.setFont(QFont("Microsoft YaHei", font_size))
    parent.resize(width, 600)
    adapter.render_config(LLMConfig(pp_strategy=strategy, pp_enable_polish=True))
    settle_layout(layout_app)

    scroll = view.controls.tabs.currentWidget()
    assert parent.width() == width
    assert scroll.horizontalScrollBar().maximum() == 0
    assert not scroll.horizontalScrollBar().isVisible()
    if strategy == "strict":
        stages = view.controls.pp_stages
        for panel in stages.findChildren(QGroupBox, options=Qt.FindChildOption.FindDirectChildrenOnly):
            assert panel.geometry().left() >= 0
            assert panel.geometry().right() < stages.width()
            assert panel.width() >= panel.minimumSizeHint().width()


def test_strict_stages_reflow_both_directions_without_resetting_values(quality_view, layout_app):
    parent, view, adapter = quality_view
    adapter.render_config(LLMConfig(pp_strategy="strict", pp_enable_polish=True, pp_strict_arbitration=True))
    stages = view.controls.pp_stages
    for width, row_count in ((1120, 1), (720, 3), (1120, 1)):
        parent.resize(width, 600)
        settle_layout(layout_app)
        assert len({panel.y() for panel in stages._panels}) == row_count
        assert view.controls.pp_polish_check.isChecked()
        assert view.controls.pp_strict_mode_check.isChecked()
        assert view.controls.tabs.currentWidget().horizontalScrollBar().maximum() == 0
