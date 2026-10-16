"""A configured model name must not hide the recovery path for missing credentials."""

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.service_readiness import service_availability
from transbridge.ui.tools.smart_assistant.onboarding_view import OnboardingView
from transbridge.ui.tools.smart_assistant.panel_header import AssistantPanelHeader
from transbridge.ui.tools.smart_assistant.window_presentation import AssistantWindowPresentation


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_name_without_key_keeps_settings_discoverable_until_configuration_is_ready(qapp):
    panel = QWidget()
    panel.is_disposed = False
    config = LLMConfig(model="example-model")
    header = AssistantPanelHeader()
    presentation = AssistantWindowPresentation(panel, header, lambda: service_availability(config))
    onboarding = OnboardingView()
    opened = []
    onboarding.configure_navigation(open_settings=lambda: opened.append("settings"))
    try:
        assert not header.model_configured
        assert "待配置" in header._model._value.full_text
        assert "API Key" in header.accessibleDescription()
        onboarding.set_state(
            model_configured=header.model_configured,
            project_name="Project",
            selected_count=2,
            service_message=header.service_availability.message,
        )
        assert not onboarding.configure_button.isHidden()
        assert "API Key" in onboarding.description.text()
        assert not onboarding.quick.isEnabled()
        onboarding.configure_button.click()
        assert opened == ["settings"]
        config.api_key = "secret"
        qapp.sendEvent(panel, QEvent(QEvent.Type.WindowActivate))
        assert header.model_configured
        assert header._model._value.full_text == "example-model"
        config.api_key = ""
        presentation.refresh()
        assert not header.model_configured
        assert "secret" not in header.accessibleDescription()
    finally:
        panel.close()
        header.close()
        onboarding.close()
