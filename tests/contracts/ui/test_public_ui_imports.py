from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.main_window import MainWindow, _AutoSaveManager
from transbridge.ui.projection_types import CollectionSlot as ProjectionCollectionSlot
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget
from transbridge.ui.workbench.step2 import Step2PreviewWidget


def test_fr25_public_ui_imports_remain_available() -> None:
    assert AppContext.__name__ == "AppContext"
    assert MainWindow.__name__ == "MainWindow"
    assert _AutoSaveManager.__name__ == "_AutoSaveManager"
    assert Step2PreviewWidget.__name__ == "Step2PreviewWidget"
    assert AITranslatorWindow.__name__ == "AITranslatorWindow"
    assert ChatWidget.__name__ == "ChatWidget"


def test_collection_slot_old_path_is_a_compatibility_export() -> None:
    assert CollectionSlot is ProjectionCollectionSlot
