from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget
import pytest

from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView, WindowConfigView


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Callbacks:
    def __init__(self) -> None:
        self.settings_requests = 0

    def on_provider_changed(self) -> None:
        return None

    def on_embed_provider_changed(self) -> None:
        return None

    def on_embedding_mode_activated(self) -> None:
        return None

    def on_embedding_api_provider_activated(self, _index: int) -> None:
        return None

    def on_manage_embedding_models(self) -> None:
        return None

    def on_test_connection(self, _target: str = "llm") -> None:
        return None

    def browse_file(self, *_args: object) -> None:
        return None

    def on_view_terms(self) -> None:
        return None

    def on_open_history(self) -> None:
        return None

    def on_batch_start(self) -> None:
        return None

    def on_open_settings(self) -> None:
        self.settings_requests += 1

    def on_start(self) -> None:
        return None

    def on_mode_changed(self) -> None:
        return None

    def update_estimate(self) -> None:
        return None

    def update_quick_run(self) -> None:
        return None

    def on_pp_enable_changed(self) -> None:
        return None

    def on_polish_changed(self) -> None:
        return None


def _view(qapp: QApplication) -> tuple[QWidget, AITranslatorView, _Callbacks]:
    parent = QWidget()
    callbacks = _Callbacks()
    view = AITranslatorView(parent, callbacks)
    parent.show()
    qapp.processEvents()
    return parent, view, callbacks


def test_single_ai_view_exposes_four_visible_task_pages_without_legacy_entries(qapp: QApplication) -> None:
    parent, view, _callbacks = _view(qapp)
    controls = view.controls

    assert parent.property("tbTaskDialog") is True
    assert view._context_label.full_text.startswith("处理范围 · 当前内容")
    assert controls.tabs.property("tbComponentKind") == "tabs"
    assert view._task_surface.property("tbTaskSurface") is True
    assert controls.mode_translate.property("tbTaskSegment") is True
    assert [controls.tabs.tabText(index) for index in range(controls.tabs.count())] == [
        "基础配置",
        "术语库",
        "质量处理",
        "运行参数",
    ]
    assert controls.tabs.isVisible()
    assert controls.tabs.accessibleName() == "AI 当前内容任务配置"
    assert not controls.advanced_btn.isVisible()
    assert not controls.batch_btn.isVisible()
    visible_button_texts = {button.text() for button in parent.findChildren(QPushButton) if button.isVisible()}
    assert "高级配置…" not in visible_button_texts
    assert "批量翻译…" not in visible_button_texts

    parent.close()


def test_single_ai_service_summary_is_secret_free_and_settings_button_emits_intent(qapp: QApplication) -> None:
    parent, view, callbacks = _view(qapp)
    secret = "secret-never-render"
    config = LLMConfig(
        provider="openai_compatible",
        model="deepseek-chat",
        api_key=secret,
    )
    adapter = WindowConfigView(view, callbacks, lambda: None)

    adapter.render_config(config)
    qapp.processEvents()
    summary = view.controls.service_summary_label.full_text

    assert "deepseek-chat" in summary
    assert secret not in summary
    assert "API Key" not in summary
    assert view.controls.settings_btn.parent().property("tbTaskServiceBar") is True
    assert view.controls.start_btn.parent().property("tbTaskFooter") is True
    assert view.controls.start_btn.property("tbTaskPrimary") is True
    view.controls.settings_btn.click()
    assert callbacks.settings_requests == 1

    parent.close()
