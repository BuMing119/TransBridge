"""Configuration notifications must not redo synchronous estimates during launch."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transbridge.ui.tools.ai_translator._window_actions import update_window_quick_run
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow


@pytest.mark.parametrize("action", ["update_estimate", "request_task_refresh", "quick_run"])
def test_launch_notifications_never_rescan_sources_or_preflight(action):
    controls = SimpleNamespace(start_btn=Mock(), preflight_label=Mock())
    window = SimpleNamespace(
        _task_launch=object(),
        _task_refresh_timer=Mock(),
        _view=SimpleNamespace(controls=controls),
        _custom_profiles=Mock(),
        _config_presenter=Mock(),
        _task_sources=Mock(side_effect=AssertionError("must not rescan live entries during launch")),
    )
    window.request_task_refresh = lambda: AITranslatorWindow.request_task_refresh(window)
    if action == "quick_run":
        update_window_quick_run(window)
    else:
        getattr(AITranslatorWindow, action)(window)
    window._task_sources.assert_not_called()
    window._config_presenter.build.assert_not_called()
    window._task_refresh_timer.start.assert_not_called()
    controls.start_btn.setEnabled.assert_called_with(False)
    controls.preflight_label.set_full_text.assert_called_with("正在准备 AI 任务…")
