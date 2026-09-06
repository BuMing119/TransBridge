from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
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
        self.source_requests = 0
        self.preset_requests = 0
        self.import_requests = 0

    def on_sources_changed(self) -> None:
        self.source_requests += 1

    def on_save_task_preset(self) -> None:
        self.preset_requests += 1

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

    def on_save_term_source_as_scheme(self) -> None:
        self.import_requests += 1

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
    assert view._context_label.full_text == "选择处理内容，配置本次 AI 任务"
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
    assert controls.tabs.accessibleName() == "AI 任务配置"
    assert controls.naming_scheme_combo.accessibleName() == "本次采用的译名方案"
    assert controls.naming_scheme_combo.currentText() == "保持当前译名"
    assert not controls.naming_scheme_combo.isEnabled()
    assert controls.naming_scheme_manage_btn.text() == "管理方案…"
    assert "术语来源" in controls.naming_scheme_status_label.text()
    assert controls.save_term_source_as_scheme_btn.text() == "从选中来源创建译名方案…"
    assert controls.priority_list.item(0).data(Qt.ItemDataRole.UserRole) == "dynamic"
    assert view.sources_panel.isVisible()
    assert view.sources_panel.selected_slots() == []
    assert view._save_preset_btn.isVisible()
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


def test_unified_task_uses_project_context_and_routes_source_and_preset_actions(qapp: QApplication) -> None:
    parent = QWidget()
    slot = SimpleNamespace(label="Content.esp", collection=[object()])
    parent._ctx = SimpleNamespace(project_name="Test project", slots={"content": slot}, active_slot=slot)
    callbacks = _Callbacks()
    view = AITranslatorView(parent, callbacks)
    parent.show()
    qapp.processEvents()

    assert view._context_label.full_text == "当前工程 · Test project"
    assert view.sources_panel.selected_slots() == [slot]
    view.sources_panel.clear_button.click()
    assert callbacks.source_requests == 1
    view._save_preset_btn.click()
    assert callbacks.preset_requests == 1
    view.controls.save_term_source_as_scheme_btn.click()
    assert callbacks.import_requests == 1
    assert view.sources_panel.geometry().right() < view._task_surface.geometry().left()
    parent.close()
