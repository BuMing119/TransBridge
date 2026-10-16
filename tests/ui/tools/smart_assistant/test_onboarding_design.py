"""Readiness changes lead to available navigation and appropriate quick actions."""

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.tools.smart_assistant.onboarding_view import OnboardingView


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_missing_configuration_offers_real_navigation_then_project_actions(qapp):
    view = OnboardingView()
    routes = []
    view.configure_navigation(
        open_settings=lambda: routes.append("ai_service"), open_project=lambda: routes.append("project_open")
    )
    view.show()
    try:
        assert view.configure_button.isVisible()
        assert not view.quick.isEnabled()
        view.configure_button.click()
        view.set_state(model_configured=True, project_name="", selected_count=0)
        assert view.configure_button.isHidden()
        assert view.open_project_button.isVisible()
        assert view.quick.isEnabled()
        assert not view.quick._buttons[0].isEnabled()
        assert view.quick._buttons[2].isEnabled()
        view.open_project_button.click()
        assert routes == ["ai_service", "project_open"]
        view.set_state(model_configured=True, project_name="中文项目", selected_count=0)
        assert view.open_project_button.isHidden()
        assert not view.quick._buttons[0].isEnabled()
        assert view.quick._buttons[1].isEnabled()
        assert "选择词条" in view.description.text()
        view.set_state(model_configured=True, project_name="中文项目", selected_count=3)
        assert view.quick._buttons[0].isEnabled()
        assert "3 条" in view.title.text()
    finally:
        view.close()


def test_missing_navigation_port_does_not_show_fake_buttons(qapp):
    view = OnboardingView()
    view.show()
    try:
        assert view.configure_button.isHidden()
        assert view.open_project_button.isHidden()
        assert "设置 → AI 服务" in view.description.text()
        view.set_state(model_configured=True, project_name="", selected_count=0)
        assert "主窗口打开项目" in view.description.text()
    finally:
        view.close()
