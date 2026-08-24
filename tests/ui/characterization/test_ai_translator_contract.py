from __future__ import annotations

from types import SimpleNamespace

from transbridge.ui.tools.ai_translator import ai_translator_window as module
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow


def test_open_without_loaded_collection_warns_once_and_returns_none(monkeypatch) -> None:
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    result = AITranslatorWindow.open_for_translation(SimpleNamespace(slots={}), None)

    assert result is None
    assert warnings == [("AI 翻译", "请先加载插件。")]
