from __future__ import annotations

from transbridge.ui.main_window import MainWindow, _AutoSaveManager
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget
from transbridge.ui.workbench.step1 import Step1SourceWidget
from transbridge.ui.workbench.step2 import Step2PreviewWidget


def test_historical_ui_facades_remain_importable_with_public_intents() -> None:
    assert MainWindow.__name__ == "MainWindow"
    assert _AutoSaveManager.__name__ == "_AutoSaveManager"
    assert Step1SourceWidget.__name__ == "Step1SourceWidget"
    assert Step2PreviewWidget.__name__ == "Step2PreviewWidget"
    assert AITranslatorWindow.__name__ == "AITranslatorWindow"
    assert ChatWidget.__name__ == "ChatWidget"
    assert callable(ChatWidget.add_user_bubble)
    assert callable(ChatWidget.add_assistant_bubble)


def test_main_window_exposes_composition_ports_without_coordinator_private_access() -> None:
    for name in (
        "context",
        "workbench",
        "project_commands",
        "runtime_context",
        "start_foreground_task",
        "save_current_project_async",
    ):
        assert hasattr(MainWindow, name)
