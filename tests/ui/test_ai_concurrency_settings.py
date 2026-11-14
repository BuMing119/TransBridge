import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.ui.settings.ai_service_page import AiServicePage


@pytest.mark.parametrize("value", [None, 3, 20, 50, 100, 128])
def test_service_page_uses_shared_default_and_preserves_explicit_values(value):
    app = QApplication.instance() or QApplication([])
    config = SimpleNamespace() if value is None else LLMConfig(max_concurrent=value)
    page = AiServicePage(config)
    try:
        assert page.concurrent_spin.value() == (100 if value is None else value)
        assert page.concurrent_spin.maximum() == 128
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_service_provider_switch_only_adjusts_default_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    app = QApplication.instance() or QApplication([])
    config = LLMConfig()
    page = AiServicePage(config)
    try:
        assert page.output_tokens_spin.specialValueText() == "自动"
        assert "8,192" in page.output_tokens_spin.toolTip()
        anthropic = page.provider_combo.findData("anthropic")
        openai = page.provider_combo.findData("openai_compatible")
        page.provider_combo.setCurrentIndex(anthropic)
        assert page.base_url_edit.text() == ""
        assert page.base_url_edit.placeholderText() == "https://api.anthropic.com"
        page.apply_to_draft()
        assert config.provider == "anthropic" and config.base_url == ""

        page.provider_combo.setCurrentIndex(openai)
        assert page.base_url_edit.text() == "https://api.openai.com/v1"
        page.base_url_edit.setText("https://proxy.example/service")
        page.provider_combo.setCurrentIndex(anthropic)
        assert page.base_url_edit.text() == "https://proxy.example/service"
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
