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
